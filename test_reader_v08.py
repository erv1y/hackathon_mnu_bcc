"""Offline regression tests; no Gemini calls."""
from pathlib import Path
from tempfile import TemporaryDirectory
from core.document_reader import read_document
from core.models import Requirement
from core.run_metadata import id_mapping

with TemporaryDirectory() as tmp:
    path = Path(tmp) / 'sample.md'
    text = '# Functional\n\nFR-01: Limit is 100.\n\n## API\n| Name | Type |\n| --- | --- |\n| limit | integer |\n\n```python\n# not a heading\n```\n'
    path.write_text(text, encoding='utf-8')
    sections = read_document(path)
    assert len(sections) == 2
    assert sections[0]['paragraph'] == 1
    assert sections[1]['line_start'] == 5
    assert '| limit | integer |' in sections[1]['text']
    assert '# not a heading' in sections[1]['text']
    for s in sections:
        assert s['text'] == '\n'.join(text.splitlines()[s['line_start']-1:s['line_end']])
    r = Requirement(id='FR-01', type='functional', text='Limit is 100.', source=path.name, paragraph=1)
    a = id_mapping([r], sections)[0]
    assert a['line_start'] == a['line_end'] == 3
    assert a['original_id'] == 'FR-01'
    from docx import Document
    doc = Document()
    doc.add_paragraph('A requirement')
    table = doc.add_table(rows=1, cols=2)
    table.cell(0,0).text = 'limit'; table.cell(0,1).text = 'integer'
    path = Path(tmp) / 'sample.docx'; doc.save(path)
    sections = read_document(path)
    assert len(sections) == 2 and sections[1]['text'] == 'limit | integer'
    print('[PASS] Markdown sections, tables, code fences, exact line references and DOCX rows')
