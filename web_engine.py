"""Read original uploads and run the shared graph off the ASGI event loop."""
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from secrets import token_urlsafe
from threading import Lock, Timer
from zipfile import ZipFile

from fastapi import HTTPException
from langgraph.checkpoint.memory import InMemorySaver

from audit_documents import DocumentBundle, DocumentSource, safe_file_name
from core.document_reader import read_document
from orchestrator import document_key, run_validation
from web_report import AGENTS, adapt_report

EXTENSIONS = {".md", ".txt", ".pdf", ".docx"}
CACHE_LOCK = Lock()
WINDOWS_UNSAFE = '<>:"/\\|?*'


class AuditRetries:
    """Short-lived, single-use checkpoints for Deep runs without disk consent."""
    def __init__(self, limit=4, lifetime=600):
        self.limit = limit
        self.lifetime = lifetime
        self.entries = OrderedDict()
        self.lock = Lock()

    def discard(self, token):
        with self.lock:
            self.entries.pop(token, None)

    def save(self, fingerprint, checkpoint):
        token = token_urlsafe(32)
        expiration = Timer(self.lifetime, self.discard, args=(token,))
        expiration.daemon = True
        with self.lock:
            if len(self.entries) >= self.limit:
                _, (_, _, oldest_expiration) = self.entries.popitem(last=False)
                oldest_expiration.cancel()
            self.entries[token] = (fingerprint, checkpoint, expiration)
            expiration.start()
        return token

    def take(self, token, fingerprint):
        with self.lock:
            entry = self.entries.get(token)
            if not entry or entry[0] != fingerprint:
                raise HTTPException(409, "The Auditor retry has expired, or the input has changed.")
            _, checkpoint, expiration = self.entries.pop(token)
            expiration.cancel()
            return checkpoint


AUDIT_RETRIES = AuditRetries()


def upload_name(name):
    name = safe_file_name(name)
    stem = Path(name).stem.split(".")[0].upper()
    if (Path(name).suffix.lower() not in EXTENSIONS
            or any(char in WINDOWS_UNSAFE for char in name)
            or stem in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}):
        raise HTTPException(400, "Unsupported filename or document type (PDF, DOCX, Markdown, TXT).")
    return name


def inspect_file(path):
    if path.suffix.lower() == ".docx":
        with ZipFile(path) as package:
            members = package.infolist()
            if len(members) > 2000 or sum(item.file_size for item in members) > 40 * 1024 * 1024:
                raise HTTPException(413, "DOCX expanded size limit exceeded.")
    return read_document(path)


@contextmanager
def sources(items, max_chars) -> Iterator:
    paths, sections, sources, anchors = [], [], [], {}
    total = 0
    with TemporaryDirectory(prefix="requiragents-") as folder:
        for filename, raw, role in items:
            path = Path(folder) / filename
            path.write_bytes(raw)
            paths.append(path)
            try:
                local = inspect_file(path)
                editing = path.suffix.lower() in {".txt", ".md"}
                text = path.read_text(encoding="utf-8-sig") if editing else "\n\n".join(s["text"] for s in local)
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(400, "Unable to read this document. Scanned PDF files require OCR.") from None
            total += len(text)
            if total > max_chars:
                raise HTTPException(413, "Extracted document text exceeds the character limit.")
            offset = 0
            for section in local:
                start = text.find(section["text"], offset) if editing else offset
                if start >= 0:
                    end = start + len(section["text"])
                    anchors[(filename, section["page"], section["paragraph"])] = (start, end)
                    offset = end + (0 if editing else 2)
            sections.extend(local)
            blank_pages = [str(s["page"]) for s in local if s["page"] is not None and not s["text"].strip()]
            warnings = ("PDF pages without extractable text: " + ", ".join(blank_pages),) if blank_pages else ()
            sources.append(DocumentSource(text, filename, role, warnings))
        yield paths, sections, DocumentBundle(sources), anchors


def analyze(items, settings, language, mode, context, max_chars, emit, *, use_cache=False, retry_token=None):
    timings, started = {}, {}
    with sources(items, max_chars) as (paths, sections, bundle, anchors):
        emit({"type": "document_ready", "source": {
            "format": "PACKAGE" if len(paths) > 1 else paths[0].suffix[1:].upper(),
            "requirements": "—", "section_count": len(sections), "sections": [], "warnings": list(bundle.warnings),
            "files": bundle.manifest,
        }})

        def on_node(node, status):
            key = AGENTS[node][0]
            if status == "started":
                started[key] = perf_counter()
            elif status not in {"retrying", "reused"}:
                timings[key] = round(perf_counter() - started[key], 2)
            event = {"type": {"started": "agent_start", "retrying": "agent_retry",
                              "reused": "agent_reused"}.get(status, "agent_done"),
                     "key": key, "ok": status in {"started", "retrying", "reused", "completed"}}
            if status.startswith("auditor_"):
                event["code"] = status
            emit(event)

        profile = settings.profile
        context = "\n\n".join(filter(None, (
            f"Validation profile {profile.id}: {profile.context}",
            profile.guidance,
            context,
        )))
        memory_checkpoint = None
        if mode == "deep" and not use_cache:
            fingerprint = document_key(paths, mode, context, language)
            memory_checkpoint = AUDIT_RETRIES.take(retry_token, fingerprint) if retry_token else InMemorySaver()
        with CACHE_LOCK if use_cache else nullcontext():
            result = run_validation(paths, mode=mode, context=context, sections=sections,
                                    node_callback=on_node, write_reports=False, use_cache=use_cache,
                                    memory_checkpoint=memory_checkpoint, language=language)
        if bundle.warnings and hasattr(result, "audit"):
            result.passport["source_warnings"] = list(bundle.warnings)
            result.passport["audit_metrics"]["score"] = None
            result.audit.limitations.extend(bundle.warnings)
            if result.audit.readiness == "ready":
                result.audit.readiness = "conditionally_ready"
                result.audit.executive_summary = "Проверка не охватила все страницы. " + result.audit.executive_summary
        if not getattr(result, "auditor_failure", None):
            emit({"type": "validating", "run_kind": result.passport["run_kind"]})
        report = adapt_report(result, bundle, settings, language, timings, anchors)
        if report.get("auditor_failure"):
            report["auditor_retry"] = ({"cache": True} if use_cache else
                                       {"token": AUDIT_RETRIES.save(fingerprint, memory_checkpoint)})
        return report
