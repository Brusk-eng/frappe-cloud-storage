# Copyright (c) 2026, Bhushan Barbuddhe and contributors
# For license information, please see license.txt

import base64
import tempfile
from unittest.mock import MagicMock
from urllib.parse import parse_qs, unquote, urlparse

import frappe
from frappe.tests import IntegrationTestCase

from multi_cloud_storage.backends.azure_backend import AzureBackend


def _backend_with_mock_client():
	backend = AzureBackend(
		frappe._dict(azure_private_container_name="private-c", azure_public_container_name="public-c")
	)
	backend._client = MagicMock()
	return backend, backend._client.get_blob_client.return_value


class TestAzureBackendUploadMetadata(IntegrationTestCase):
	"""Azure blob metadata must be ASCII, so upload() percent-encodes the File name."""

	def _upload(self, file_name):
		backend, blob_client = _backend_with_mock_client()
		with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
			f.write(b"%PDF-1.4 test")
			f.flush()
			backend.upload(f.name, "a/b.pdf", "application/pdf", True, file_name)
		return blob_client.upload_blob.call_args.kwargs["metadata"]["file_name"]

	def test_non_ascii_file_name_is_encoded(self):
		file_name = "Hindi_WOW हब संचालन मैनुअल.pdf"
		value = self._upload(file_name)
		self.assertTrue(value.isascii())
		self.assertEqual(unquote(value), file_name)

	def test_ascii_file_name_round_trips(self):
		self.assertEqual(unquote(self._upload("report final.pdf")), "report final.pdf")

	def test_empty_file_name(self):
		self.assertEqual(self._upload(None), "")


class TestAzureBackendGetUrl(IntegrationTestCase):
	"""get_url() must put a valid (ASCII) Content-Disposition in the SAS token for any file name."""

	def _disposition(self, file_name):
		backend, _ = _backend_with_mock_client()
		backend.config.azure_account_name = "acct"
		backend.config.signed_url_expiry_time = 300
		backend._account_key = base64.b64encode(b"k" * 32).decode()
		url = backend.get_url("a/b.pdf", file_name=file_name, bucket_type="private")
		return parse_qs(urlparse(url).query)["rscd"][0]

	def test_plain_name_is_unchanged(self):
		self.assertEqual(self._disposition("export.csv"), 'inline; filename="export.csv"')

	def test_non_ascii_name_is_valid_header(self):
		file_name = "Hindi_WOW हब संचालन मैनुअल.pdf"
		value = self._disposition(file_name)
		self.assertTrue(value.isascii())
		self.assertEqual(unquote(value.split("filename*=UTF-8''")[1]), file_name)
