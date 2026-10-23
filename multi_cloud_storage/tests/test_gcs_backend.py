# Copyright (c) 2026, Bhushan Barbuddhe and contributors
# For license information, please see license.txt

from unittest.mock import MagicMock, patch
from urllib.parse import unquote

import frappe
from frappe.tests import IntegrationTestCase

from multi_cloud_storage.backends.gcs_backend import GCSBackend


def _backend_and_blob():
	backend = GCSBackend(frappe._dict(signed_url_expiry_time=300))
	blob = MagicMock()
	backend._client = MagicMock()
	backend._client.bucket.return_value.blob.return_value = blob
	return backend, blob


@patch.object(GCSBackend, "_bucket_name", return_value="private-b")
class TestGCSBackendGetUrl(IntegrationTestCase):
	"""get_url() must send a valid (ASCII) Content-Disposition for any file name."""

	def _disposition(self, file_name, as_attachment=False):
		backend, blob = _backend_and_blob()
		backend.get_url("a/b.pdf", file_name=file_name, bucket_type="private", as_attachment=as_attachment)
		return blob.generate_signed_url.call_args.kwargs["response_disposition"]

	def test_plain_name_is_unchanged(self, _bucket_name):
		self.assertEqual(
			self._disposition("export.csv", as_attachment=True), 'attachment; filename="export.csv"'
		)

	def test_non_ascii_name_is_valid_header(self, _bucket_name):
		file_name = "Hindi_WOW हब संचालन मैनुअल.pdf"
		value = self._disposition(file_name)
		self.assertTrue(value.isascii())
		self.assertEqual(unquote(value.split("filename*=UTF-8''")[1]), file_name)

	def test_no_file_name_omits_disposition(self, _bucket_name):
		backend, blob = _backend_and_blob()
		backend.get_url("a/b.pdf", file_name=None, bucket_type="private")
		self.assertNotIn("response_disposition", blob.generate_signed_url.call_args.kwargs)
