"""Read documents while preserving the existing source/page/paragraph contract."""
from pathlib import Path
import re
import pymupdf
from docx import Document


def read_pdf(file_path):
    with pymupdf.open(file_path) as doc:
        return [dict(source=Path(file_path).name, page=i, paragraph=None,
                     text=page.get_text(sort=True).strip())
                for i, page in enumerate(doc, 1)]


def read_docx(file_path):
    doc = Document(file_path)
    sections = []
    # Keep original paragraph numbering; append table rows with unique anchors.
    for i, p in enumerate(doc.paragraphs, 1):
        if p.text.strip():
            sections.append(dict(source=Path(file_path).name, page=None,
                                 paragraph=i, text=p.text.strip()))
    anchor = len(doc.paragraphs)
    for table_no, table in enumerate(doc.tables, 1):
        for row_no, row in enumerate(table.rows, 1):
            anchor += 1
            text = ' | '.join(cell.text for cell in row.cells)
            if text.replace('|', '').strip():
                sections.append(dict(source=Path(file_path).name, page=None,
                                     paragraph=anchor, text=text,
                                     section=f'Таблица {table_no}, строка {row_no}'))
    return sections


def read_text(file_path):
    path = Path(file_path)
    lines = path.read_text(encoding='utf-8-sig').splitlines()
    sections = []
    stack = []
    start = 0
    heading = 'Начало документа'
    fence = None

    def flush(end):
        a, b = start, end
        while a < b and not lines[a].strip():
            a += 1
        while b > a and not lines[b-1].strip():
            b -= 1
        if a < b:
            sections.append(dict(source=path.name, page=None,
                                 paragraph=len(sections)+1,
                                 section=heading, line_start=a+1, line_end=b,
                                 text='\n'.join(lines[a:b])))

    for i, line in enumerate(lines):
        if path.suffix.lower() != '.md':
            continue
        f = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if f:
            marker, tail = f.groups()
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence) and not tail.strip():
                fence = None
            continue
        if fence is not None:
            continue
        h = re.match(r'^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$', line)
        if h:
            flush(i)
            level, name = len(h[1]), h[2]
            stack = [(n, title) for n, title in stack if n < level]
            stack.append((level, name))
            heading = ' / '.join(title for _, title in stack)
            start = i
    flush(len(lines))
    return sections


def read_document(file_path):
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f'File not found: {path}')
    readers = {'.pdf': read_pdf, '.docx': read_docx, '.md': read_text, '.txt': read_text}
    if path.suffix.lower() not in readers:
        raise ValueError(f'Unsupported format: {path.suffix}')
    sections = readers[path.suffix.lower()](path)
    if not any(s['text'].strip() for s in sections):
        raise ValueError('No readable text found. OCR may be required.')
    return sections


if __name__ == '__main__':
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else input('Enter document path: ').strip().strip('"')
    sections = read_document(path)
    print(f'Sections extracted: {len(sections)}')
    for s in sections:
        print('\n' + '=' * 50)
        print({k:v for k,v in s.items() if k != 'text'})
        print(s['text'])
