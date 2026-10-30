# Copyright (c) 2026, Bhushan Barbuddhe and contributors
# For license information, please see license.txt

from unittest.mock import MagicMock
from urllib.parse import quote, unquote

import boto3
import frappe
from botocore.exceptions import ClientError, ParamValidationError
from frappe.tests import IntegrationTestCase

from multi_cloud_storage.backends.s3_backend import S3Backend


def _config():
	return frappe._dict(
		s3_private_bucket_name="private-bucket",
		s3_public_bucket_name="public-bucket",
		s3_region_name="ap-south-1",
	)


def _backend_with_mock_client():
	backend = S3Backend(_config())
	mock_client = MagicMock()
	backend._client = mock_client
	return backend, mock_client


class TestS3BackendObjectExists(IntegrationTestCase):
	"""
	object_exists() is the small-scale, single-key primitive (manual linking, the
	verify=True default path). Bulk reconciliation never calls this per row -- see
	TestS3BackendListKeysPage below for the mechanism that actually scales.
	"""

	def test_true_on_successful_head(self):
		backend, mock_client = _backend_with_mock_client()
		mock_client.head_object.return_value = {}
		self.assertTrue(backend.object_exists("a/b.pdf", "private"))
		mock_client.head_object.assert_called_once_with(Bucket="private-bucket", Key="a/b.pdf")

	def test_false_on_404(self):
		backend, mock_client = _backend_with_mock_client()
		mock_client.head_object.side_effect = ClientError(
			{"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
		)
		self.assertFalse(backend.object_exists("a/missing.pdf", "private"))

	def test_reraises_non_404_errors(self):
		backend, mock_client = _backend_with_mock_client()
		mock_client.head_object.side_effect = ClientError(
			{"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject"
		)
		with self.assertRaises(ClientError):
			backend.object_exists("a/forbidden.pdf", "private")

	def test_uses_public_bucket_for_public_bucket_type(self):
		backend, mock_client = _backend_with_mock_client()
		mock_client.head_object.return_value = {}
		backend.object_exists("a/b.pdf", "public")
		mock_client.head_object.assert_called_once_with(Bucket="public-bucket", Key="a/b.pdf")


class TestS3BackendListKeysPage(IntegrationTestCase):
	"""
	list_keys_page() is what scan_bucket_index runs against instead of per-key HEAD
	requests: one call covers up to 1,000 objects, which is what keeps a bulk scan
	bucket-size-bound (thousands of calls) rather than row-count-bound (millions).
	"""

	def test_single_untruncated_page(self):
		backend, mock_client = _backend_with_mock_client()
		mock_client.list_objects_v2.return_value = {
			"Contents": [
				{"Key": "a/b.pdf", "Size": 100, "LastModified": "2026-01-01"},
				{"Key": "a/c.pdf", "Size": 200, "LastModified": "2026-01-02"},
			],
			"IsTruncated": False,
		}
		page = backend.list_keys_page(bucket_type="private", page_size=1000)
		self.assertEqual(page["objects"], [("a/b.pdf", 100, "2026-01-01"), ("a/c.pdf", 200, "2026-01-02")])
		self.assertIsNone(page["continuation_token"])
		self.assertFalse(page["is_truncated"])
		mock_client.list_objects_v2.assert_called_once_with(Bucket="private-bucket", MaxKeys=1000)

	def test_truncated_page_carries_continuation_token(self):
		backend, mock_client = _backend_with_mock_client()
		mock_client.list_objects_v2.return_value = {
			"Contents": [{"Key": "a/b.pdf", "Size": 1, "LastModified": None}],
			"IsTruncated": True,
			"NextContinuationToken": "token-123",
		}
		page = backend.list_keys_page(bucket_type="private", page_size=1)
		self.assertEqual(page["continuation_token"], "token-123")
		self.assertTrue(page["is_truncated"])

	def test_continuation_token_and_prefix_are_forwarded(self):
		backend, mock_client = _backend_with_mock_client()
		mock_client.list_objects_v2.return_value = {"Contents": [], "IsTruncated": False}
		backend.list_keys_page(bucket_type="public", prefix="beam/", continuation_token="tok", page_size=500)
		mock_client.list_objects_v2.assert_called_once_with(
			Bucket="public-bucket", MaxKeys=500, Prefix="beam/", ContinuationToken="tok"
		)

	def test_empty_bucket_returns_no_objects(self):
		backend, mock_client = _backend_with_mock_client()
		mock_client.list_objects_v2.return_value = {"IsTruncated": False}
		page = backend.list_keys_page(bucket_type="private")
		self.assertEqual(page["objects"], [])
		self.assertFalse(page["is_truncated"])


class TestS3BackendGetUrl(IntegrationTestCase):
	"""
	get_url()'s Content-Disposition must be explicit either way -- "inline" for preview
	use cases, "attachment" for forced downloads -- never the old bare "filename=" that
	left the outcome up to whichever way a given browser guesses.
	"""

	def test_as_attachment_true_forces_download(self):
		backend, mock_client = _backend_with_mock_client()
		mock_client.generate_presigned_url.return_value = "https://signed.example.com/x"
		backend.get_url("a/export.csv", file_name="export.csv", bucket_type="private", as_attachment=True)
		_, kwargs = mock_client.generate_presigned_url.call_args
		self.assertEqual(kwargs["Params"]["ResponseContentDisposition"], 'attachment; filename="export.csv"')

	def test_as_attachment_false_serves_inline(self):
		backend, mock_client = _backend_with_mock_client()
		mock_client.generate_presigned_url.return_value = "https://signed.example.com/x"
		backend.get_url("a/photo.jpg", file_name="photo.jpg", bucket_type="private", as_attachment=False)
		_, kwargs = mock_client.generate_presigned_url.call_args
		self.assertEqual(kwargs["Params"]["ResponseContentDisposition"], 'inline; filename="photo.jpg"')

	def test_no_file_name_omits_content_disposition(self):
		backend, mock_client = _backend_with_mock_client()
		mock_client.generate_presigned_url.return_value = "https://signed.example.com/x"
		backend.get_url("a/b.pdf", file_name=None, bucket_type="private")
		_, kwargs = mock_client.generate_presigned_url.call_args
		self.assertNotIn("ResponseContentDisposition", kwargs["Params"])


class TestS3BackendUploadMetadata(IntegrationTestCase):
	"""
	S3 rejects non-ASCII object metadata, so upload() must percent-encode a non-ASCII File name.
	The upload tests mock the client and assert the Metadata sent; the last test runs a real
	boto3 client so botocore's own ASCII validation applies.
	"""

	def _upload(self, file_name, expected):
		backend, mock_client = _backend_with_mock_client()
		self.assertEqual(
			backend.upload("/tmp/x.pdf", "a/b.pdf", "application/pdf", True, file_name), "a/b.pdf"
		)
		_, kwargs = mock_client.upload_file.call_args
		extra = kwargs["ExtraArgs"]
		self.assertEqual(extra["Metadata"], {"file_name": expected})
		self.assertEqual(extra["ContentType"], "application/pdf")
		self.assertTrue(extra["Metadata"]["file_name"].isascii())

	def test_non_ascii_file_name_is_sent_percent_encoded(self):
		name = "Hindi_WOW हब संचालन मैनुअल.pdf"
		self._upload(name, quote(name))

	def test_ascii_file_name_is_sent_unchanged(self):
		self._upload("report final.pdf", "report final.pdf")

	def test_empty_file_name_sends_empty_metadata(self):
		self._upload(None, "")

	def test_raw_non_ascii_metadata_is_rejected_by_botocore(self):
		client = boto3.client(
			"s3", region_name="ap-south-1", aws_access_key_id="test", aws_secret_access_key="test"
		)
		with self.assertRaises(ParamValidationError) as ctx:
			client.put_object(Bucket="b", Key="k", Body=b"x", Metadata={"file_name": "हब.pdf"})
		self.assertIn("ASCII", str(ctx.exception))

	def test_non_ascii_file_name_gets_valid_content_disposition(self):
		backend, mock_client = _backend_with_mock_client()
		mock_client.generate_presigned_url.return_value = "https://signed.example.com/x"
		file_name = "Hindi_WOW हब संचालन मैनुअल.pdf"
		backend.get_url("a/b.pdf", file_name=file_name, bucket_type="private")
		_, kwargs = mock_client.generate_presigned_url.call_args
		value = kwargs["Params"]["ResponseContentDisposition"]
		self.assertTrue(value.isascii())
		self.assertEqual(unquote(value.split("filename*=UTF-8''")[1]), file_name)
