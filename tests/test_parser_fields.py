import unittest

from app.parser import parse_detail


def detail_from_description(description: str):
    html = f"""
    <html>
      <head><meta property="og:title" content="Example"></head>
      <body><div class="fdesc full-text">{description}</div></body>
    </html>
    """
    return parse_detail(html, "https://mir-modeley.com/news/example/2026-01-01-1")


class ParserFieldTests(unittest.TestCase):
    def test_format_field_keeps_embedded_b4_paper_size(self):
        record = detail_from_description("Формат: JPG в RAR, B4 (300dpi)")

        self.assertEqual(record.file_format, "JPG")
        self.assertEqual(record.paper_format, "B4")

    def test_format_field_keeps_embedded_c4_paper_size(self):
        record = detail_from_description("Формат: JPG в RAR, C4 (300dpi)")

        self.assertEqual(record.file_format, "JPG")
        self.assertEqual(record.paper_format, "C4")

    def test_format_field_normalizes_cyrillic_a4_paper_size(self):
        record = detail_from_description("Формат: JPG в RAR, А4 (600dpi)")

        self.assertEqual(record.file_format, "JPG")
        self.assertEqual(record.paper_format, "A4")

    def test_instruction_sheet_label_uses_number_before_slash(self):
        record = detail_from_description("Листов с инструкцией/выкройки: 8/4")

        self.assertEqual(record.total_pages, "8")


if __name__ == "__main__":
    unittest.main()
