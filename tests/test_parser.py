import io
import unittest

from docx import Document
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from parser import DocumentLimitError, ParseError, extract_text_from_bytes, normalize_text, parse_document


def make_pdf(spec: str = "", *, blank_page: bool = False) -> bytes:
    writer = PdfWriter()
    if spec:
        page = writer.add_blank_page(width=300, height=200)
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
        })
        content = DecodedStreamObject()
        content.set_data(f"BT /F1 10 Tf 15 100 Td ({spec}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = content
    if blank_page or not spec:
        writer.add_blank_page(width=300, height=200)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


class ParserTests(unittest.TestCase):
    def test_normalize_text(self):
        self.assertEqual(normalize_text("  Пункт 1  \r\n\r\n\r\nПункт 2\x00 "), "Пункт 1\n\nПункт 2")

    def test_cp1251_text(self):
        text = "Требование: повторный платёж не создаёт второе списание."
        self.assertEqual(extract_text_from_bytes("spec.txt", text.encode("cp1251")), text)

    def test_docx_keeps_paragraph_and_table_order(self):
        doc = Document()
        doc.add_paragraph("Перед таблицей")
        table = doc.add_table(rows=1, cols=2)
        table.cell(0, 0).text = "Статус"
        table.cell(0, 1).text = "Описание"
        doc.add_paragraph("После таблицы")
        stream = io.BytesIO()
        doc.save(stream)

        result = extract_text_from_bytes("spec.docx", stream.getvalue())
        self.assertLess(result.index("Перед таблицей"), result.index("Статус | Описание"))
        self.assertLess(result.index("Статус | Описание"), result.index("После таблицы"))

    def test_empty_file_is_rejected(self):
        with self.assertRaisesRegex(ParseError, "пустой"):
            extract_text_from_bytes("spec.txt", b"")

    def test_legacy_word_format_has_actionable_error(self):
        with self.assertRaisesRegex(ParseError, "DOCX или PDF"):
            extract_text_from_bytes("spec.doc", b"not-empty")

    def test_separate_cells_with_identical_text_are_preserved(self):
        doc = Document()
        row = doc.add_table(rows=1, cols=2).rows[0]
        row.cells[0].text = "100"
        row.cells[1].text = "100"
        raw = io.BytesIO()
        doc.save(raw)
        self.assertEqual(extract_text_from_bytes("limits.docx", raw.getvalue()), "100 | 100")

    def test_mixed_pdf_has_partial_extraction_warning(self):
        result = parse_document("spec.pdf", make_pdf("Payment starts with customer confirmation.", blank_page=True))
        self.assertEqual(result.text, "Payment starts with customer confirmation.")
        self.assertIn("2", result.warnings[0])

    def test_pdf_without_text_layer_is_rejected(self):
        with self.assertRaisesRegex(ParseError, "текстовый слой"):
            parse_document("blank.pdf", make_pdf())

    def test_extracted_text_is_not_truncated(self):
        with self.assertRaises(DocumentLimitError):
            parse_document("spec.txt", b"Some long document", max_chars=12)


if __name__ == "__main__":
    unittest.main()
