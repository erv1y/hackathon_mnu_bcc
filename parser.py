import io
import re
from dataclasses import dataclass
from zipfile import BadZipFile, ZipFile

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader

from languages import Language, error_text

SUPPORTED_EXTENSIONS = (".pdf", ".docx", ".txt", ".md")
MAX_PDF_PAGES = 200
MAX_DOCX_EXPANDED_BYTES = 50 * 1024 * 1024


class ParseError(ValueError):
    """Понятная пользователю ошибка разбора файла."""


class DocumentLimitError(ParseError):
    """Размер или объём документа превышает поддерживаемый лимит."""


@dataclass(frozen=True)
class ParsedSpec:
    text: str
    warnings: tuple[str, ...] = ()


def _check_text_size(text: str, max_chars: int | None, language: Language) -> None:
    if max_chars is not None and len(text) > max_chars:
        raise DocumentLimitError(error_text(language, "text_limit", limit=max_chars))


def _decode_text(raw: bytes) -> str:
    # Файлы из Windows (Блокнот, 1С, старый Word -> TXT) часто приходят в cp1251.
    for encoding in ("utf-8-sig", "utf-16", "cp1251"):
        if encoding == "utf-16" and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            continue
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _extract_pdf(raw: bytes, max_chars: int | None, language: Language) -> ParsedSpec:
    try:
        reader = PdfReader(io.BytesIO(raw))
    except Exception as exc:
        raise ParseError(error_text(language, "pdf_damaged")) from exc

    if reader.is_encrypted:
        try:
            if not reader.decrypt(""):
                raise ParseError(error_text(language, "pdf_password"))
        except ParseError:
            raise
        except Exception as exc:
            raise ParseError(error_text(language, "pdf_password")) from exc

    if len(reader.pages) > MAX_PDF_PAGES:
        raise DocumentLimitError(error_text(language, "pdf_limit", limit=MAX_PDF_PAGES))

    pages, unreadable = [], []
    extracted_chars = 0
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = normalize_text(page.extract_text() or "")
        except Exception:
            text = ""
        if not text:
            unreadable.append(index)
            continue
        extracted_chars += len(text) + bool(pages)
        if max_chars is not None and extracted_chars > max_chars:
            _check_text_size("\n".join([*pages, text]), max_chars, language)
        pages.append(text)
    text = "\n".join(pages)
    if not text:
        raise ParseError(error_text(language, "pdf_no_text"))
    warnings = ()
    if unreadable:
        numbers = ", ".join(map(str, unreadable[:10]))
        if len(unreadable) > 10:
            numbers += ", …"
        warnings = (error_text(language, "pdf_partial", pages=numbers),)
    return ParsedSpec(text, warnings)


def _table_to_text(table: Table) -> str:
    rows = []
    for row in table.rows:
        cells = []
        previous = None
        for cell in row.cells:
            value = " ".join(cell.text.split())
            # Объединенные ячейки python-docx возвращает повторно — убираем дубли.
            if cell._tc is not previous:
                cells.append(value)
            previous = cell._tc
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _extract_docx(raw: bytes, language: Language) -> str:
    try:
        with ZipFile(io.BytesIO(raw)) as package:
            if sum(info.file_size for info in package.infolist()) > MAX_DOCX_EXPANDED_BYTES:
                raise DocumentLimitError(error_text(language, "docx_limit"))
    except BadZipFile as exc:
        raise ParseError(error_text(language, "docx_damaged")) from exc
    try:
        doc = Document(io.BytesIO(raw))
    except Exception as exc:
        raise ParseError(error_text(language, "docx_damaged")) from exc

    blocks = []
    # Обходим тело документа по порядку: абзацы и таблицы (лимиты/тарифы в ТЗ часто в таблицах).
    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            text = Paragraph(child, doc).text.strip()
            if text:
                blocks.append(text)
        elif tag == "tbl":
            table_text = _table_to_text(Table(child, doc))
            if table_text:
                blocks.append(table_text)
    return "\n".join(blocks).strip()


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_document(filename: str, raw_bytes: bytes, *, max_chars: int | None = None,
                   language: Language = "ru") -> ParsedSpec:
    lower_name = (filename or "").lower()

    if not raw_bytes:
        raise ParseError(error_text(language, "file_empty"))

    warnings = ()
    if lower_name.endswith((".txt", ".md")):
        text = _decode_text(raw_bytes)
    elif lower_name.endswith(".pdf"):
        extracted = _extract_pdf(raw_bytes, max_chars, language)
        text, warnings = extracted.text, extracted.warnings
    elif lower_name.endswith(".docx"):
        text = _extract_docx(raw_bytes, language)
    elif lower_name.endswith((".doc", ".rtf", ".odt")):
        raise ParseError(error_text(language, "old_format"))
    else:
        raise ParseError(error_text(language, "unsupported"))

    text = normalize_text(text)
    if not text:
        raise ParseError(error_text(language, "extraction_empty"))
    _check_text_size(text, max_chars, language)
    return ParsedSpec(text, warnings)


def extract_text_from_bytes(filename: str, raw_bytes: bytes) -> str:
    return parse_document(filename, raw_bytes).text
