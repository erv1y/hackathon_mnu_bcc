"""Extract source spans, then construct verbatim requirements in Python."""
import json
import re
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field, ValidationError
from core.llm_client import ask_gemini
from core.document_reader import read_document
from core.models import ExtractionResult, Requirement

BASE_DIR = Path(__file__).resolve().parent.parent
PROMPT_PATH = BASE_DIR / 'prompts' / 'extractor.txt'

class SourceSpan(BaseModel):
    block: int = Field(ge=1)
    first_line: int = Field(ge=1)
    last_line: int = Field(ge=1)
    original_id: str = ''
    type: Literal['functional', 'non_functional', 'business_rule', 'acceptance_criterion', 'other']

class SpanResult(BaseModel):
    spans: list[SourceSpan]


def make_blocks(sections):
    return [{'block': i, 'section': s.get('section', ''), 'source': s['source'],
             'lines': [{'n': n, 'text': line} for n, line in enumerate(s['text'].split('\n'), 1)]}
            for i, s in enumerate(sections, 1)]


def materialize(spans, sections):
    result = []
    seen = set()
    for span in spans:
        if not 1 <= span.block <= len(sections):
            raise ValueError(f'Unknown block: {span.block}')
        s = sections[span.block - 1]
        lines = s['text'].split('\n')
        a, b = span.first_line, span.last_line
        if not 1 <= a <= b <= len(lines):
            raise ValueError(f'Invalid range: block {span.block}, {a}-{b}')
        quote = '\n'.join(lines[a-1:b]).strip()
        if not quote or all(not x.strip() or re.match(r'^\s*#{1,6}\s', x) for x in quote.splitlines()):
            raise ValueError(f'Empty or heading-only span: {span.block}:{a}-{b}')
        key = (span.block, a, b)
        if key in seen:
            continue
        seen.add(key)
        original = span.original_id.strip()
        # Only preserve a supplied ID if it is visibly present in the selected source.
        if original and not re.search(r'(?<![\w-])' + re.escape(original) + r'(?![\w-])', quote):
            print(f'[ID REVIEW] Unverified original ID; assigning system ID')
            original = ''
        result.append((key, Requirement(id=original or 'AUTO', type=span.type, text=quote,
                         source=s['source'], page=s.get('page'), paragraph=s.get('paragraph'))))
    return sorted(result, key=lambda x: x[0])


def extract_batch(sections, instruction):
    blocks = make_blocks(sections)
    error = None
    for attempt in range(1, 3):
        payload = {'blocks': blocks}
        if error:
            payload['validation_error'] = error
        response = ask_gemini(prompt=json.dumps(payload, ensure_ascii=False),
                              system_instruction=instruction, model='gemini-3.5-flash-lite',
                              response_schema=SpanResult).strip()
        if response.startswith('```'):
            response = re.sub(r'^```(?:json)?\s*', '', response, flags=re.I)
            response = re.sub(r'\s*```$', '', response)
        try:
            spans = SpanResult.model_validate_json(response).spans
            items = materialize(spans, sections)
            if not items:
                print('[COVERAGE REVIEW] No spans selected in this batch')
            return [r for _, r in items]
        except ValidationError:
            error = 'invalid_span_schema'
        except ValueError as exc:
            error = str(exc)  # materialize reports only local span indices and fixed rule text.
        print(f'[SPAN RETRY] {attempt}/2: {error}')
    raise ValueError(f'Source span validation failed: {error}')


def normalize_ids(items):
    used = {r.id for r in items if r.id != 'AUTO'}
    seen = set()
    n = 1
    out = []
    for r in items:
        if r.id == 'AUTO' or r.id in seen:
            if r.id != 'AUTO':
                print(f'[ID REVIEW] Repeated original ID; retaining text with new system ID')
            while f'AUTO-{n:03d}' in used:
                n += 1
            r = r.model_copy(update={'id': f'AUTO-{n:03d}'})
            used.add(r.id)
            n += 1
        seen.add(r.id)
        out.append(r)
    return out


def validate_output(result, sections):
    ids = [r.id for r in result.requirements]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError('Empty extraction or duplicate IDs')
    for r in result.requirements:
        if not any(s['source'] == r.source and s.get('page') == r.page
                   and s.get('paragraph') == r.paragraph and r.text in s['text'] for s in sections):
            raise ValueError(f'Unsupported requirement: {r.id}')


def extract_requirements(sections):
    print('\n[AGENT 1] Extractor 3.0 started')
    instruction = PROMPT_PATH.read_text(encoding='utf-8')
    sections = [s for s in sections if s['text'].strip()]
    batches = [sections[i:i+6] for i in range(0, len(sections), 6)]
    items = []
    for i, batch in enumerate(batches, 1):
        print(f'[BATCH] {i}/{len(batches)}')
        items.extend(extract_batch(batch, instruction))
    result = ExtractionResult(requirements=normalize_ids(items))
    validate_output(result, sections)
    covered = {(r.source,r.page,r.paragraph) for r in result.requirements}
    for s in sections:
        if (s['source'],s.get('page'),s.get('paragraph')) not in covered:
            print(f"[COVERAGE REVIEW] page={s.get('page')} paragraph={s.get('paragraph')}")
    print('[VALIDATOR] Verbatim source spans passed')
    print(f'[AGENT 1] Extracted {len(result.requirements)} requirements')
    return result

if __name__ == '__main__':
    import sys
    path = Path(sys.argv[1]) if len(sys.argv)>1 else BASE_DIR/'docs'/'test.pdf'
    result = extract_requirements(read_document(path))
    output = BASE_DIR/'reports'/'extraction_v08.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(result.model_dump_json(indent=2), encoding='utf-8')
    print(f'[SAVED] {output}')
