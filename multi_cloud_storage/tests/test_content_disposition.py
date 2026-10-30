# Copyright (c) 2026, Bhushan Barbuddhe and contributors
# For license information, please see license.txt

from urllib.parse import quote, unquote

from frappe.tests import IntegrationTestCase

from multi_cloud_storage.backends.base import build_content_disposition, build_metadata_file_name

HINDI = "Hindi_WOW हब संचालन मैनुअल.pdf"


class TestBuildContentDisposition(IntegrationTestCase):
	"""Plain names keep the legacy header; anything else gets an ASCII fallback plus filename*=."""

	def test_plain_name_is_unchanged(self):
		self.assertEqual(
			build_content_disposition("attachment", "export.csv"), 'attachment; filename="export.csv"'
		)

	def test_plain_name_with_spaces_is_unchanged(self):
		self.assertEqual(
			build_content_disposition("inline", "my report.pdf"), 'inline; filename="my report.pdf"'
		)

	def test_non_ascii_name_gets_ascii_fallback_and_encoded_name(self):
		value = build_content_disposition("inline", HINDI)
		self.assertTrue(value.isascii())
		self.assertIn('filename="Hindi_WOW_.pdf"', value)
		encoded = value.split("filename*=UTF-8''")[1]
		self.assertEqual(unquote(encoded), HINDI)

	def test_double_quote_cannot_break_out_of_the_header(self):
		value = build_content_disposition("inline", 'a"b.pdf')
		self.assertIn('filename="a_b.pdf"', value)
		self.assertIn("filename*=UTF-8''a%22b.pdf", value)

	def test_backslash_is_not_left_in_the_quoted_fallback(self):
		self.assertIn('filename="back_slash.pdf"', build_content_disposition("inline", "back\\slash.pdf"))

	def test_control_characters_cannot_inject_headers(self):
		value = build_content_disposition("inline", "x\r\nSet-Cookie: a=b.pdf")
		self.assertNotIn("\r", value)
		self.assertNotIn("\n", value)

	def test_disposition_is_passed_through(self):
		self.assertTrue(build_content_disposition("attachment", HINDI).startswith("attachment; "))


class TestBuildMetadataFileName(IntegrationTestCase):
	"""Metadata must be ASCII: plain names are unchanged, anything else is percent-encoded."""

	def test_plain_name_is_unchanged(self):
		self.assertEqual(build_metadata_file_name("report final.pdf"), "report final.pdf")

	def test_empty_or_none_is_empty(self):
		self.assertEqual(build_metadata_file_name(None), "")
		self.assertEqual(build_metadata_file_name(""), "")

	def test_non_ascii_name_is_encoded_and_ascii(self):
		value = build_metadata_file_name(HINDI)
		self.assertEqual(value, quote(HINDI))
		self.assertTrue(value.isascii())
		self.assertEqual(unquote(value), HINDI)
