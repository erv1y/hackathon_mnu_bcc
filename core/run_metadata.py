"""Per-node model provenance; context-local for parallel graph branches."""
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
import subprocess

_calls = ContextVar('model_calls', default=None)

def record_model(model, reported_version=None):
    calls = _calls.get()
    if calls is not None:
        calls.append({'requested_model': str(model), 'reported_version': str(reported_version) if reported_version else None})

def tracked(function, *args):
    calls = []
    token = _calls.set(calls)
    try:
        return function(*args), calls
    finally:
        _calls.reset(token)

def now():
    return datetime.now(timezone.utc).isoformat()

def revision():
    root = Path(__file__).resolve().parent.parent
    try:
        commit = subprocess.check_output(['git','rev-parse','HEAD'], cwd=root, stderr=subprocess.DEVNULL, text=True, timeout=5).strip()
        dirty = bool(subprocess.check_output(['git','status','--porcelain'], cwd=root, stderr=subprocess.DEVNULL, text=True, timeout=5).strip())
        return {'commit': commit, 'dirty': dirty}
    except (OSError, subprocess.SubprocessError):
        return {'commit': None, 'dirty': None}

def id_mapping(requirements, sections):
    import re
    rows = []
    for r in requirements:
        candidate = r.id.split('::', 1)[-1]
        quote = ' '.join(r.text.split())
        # Only claim an original ID if present immediately before the quote,
        # or at its beginning. Otherwise preserve the uncertainty.
        prefix = re.escape(candidate)
        pattern = r'(?<![\w-])' + prefix + r'(?![\w-])(?:[\s:.)\]–—*|-])*' + re.escape(quote)
        texts = [' '.join(s['text'].split()) for s in sections if s['source'] == r.source and s.get('page') == r.page and s.get('paragraph') == r.paragraph]
        verified = any(re.search(pattern, t) for t in texts) or bool(re.match(r'^'+prefix+r'(?![\w-])', quote))
        location = {}
        for section in sections:
            if (section['source'], section.get('page'), section.get('paragraph')) != (r.source, r.page, r.paragraph):
                continue
            if section.get('section'):
                location['section'] = section['section']
            if section.get('line_start') is not None and quote:
                span = re.search(r'\s+'.join(re.escape(word) for word in quote.split()), section['text'])
                if span:
                    location['line_start'] = section['line_start'] + section['text'].count('\n', 0, span.start())
                    location['line_end'] = section['line_start'] + section['text'].count('\n', 0, span.end())
            break
        rows.append({'report_id': r.id, 'original_id': candidate if verified else None, 'source': r.source, 'page': r.page, 'paragraph': r.paragraph, 'quote': r.text, **location})
    return rows
