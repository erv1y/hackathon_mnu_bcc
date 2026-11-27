import asyncio
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal, Optional
from urllib.parse import unquote

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
DEMO_ONLY = os.getenv("AUDIT_DEMO_ONLY") == "1"
if not DEMO_ONLY and os.getenv("AUDIT_LOAD_DOTENV", "1") != "0":
    load_dotenv(BASE_DIR / ".env")

from fastapi.exceptions import RequestValidationError
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.concurrency import run_in_threadpool  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import Response, HTMLResponse, JSONResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import Field, TypeAdapter, ValidationError  # noqa: E402
from starlette.datastructures import Headers
from core.llm_client import API_KEY, MODEL_NAME
from web_engine import analyze, upload_name
from web_report import CRITERIA, agent_list
from tempfile import TemporaryDirectory
from agents.auditor import prepare_findings, validate_audit, validate_partial_audit
from core.report_generator import generate_pdf as generate_base_pdf
from core.advanced_report import generate_pdf, generate_quick_pdf
from orchestrator import DeepPartialRunResult, DeepRunResult, QuickRunResult  # noqa: E402

from analyzer import (  # noqa: E402
    build_rules_report,
    get_settings,
)
from audit_profile import DataFileError  # noqa: E402
from audit_compare import ComparisonRequest, compare_reports  # noqa: E402
from audit_documents import DocumentBundle, DocumentSource, MAX_DOCUMENTS, PreparedDocument, safe_file_name  # noqa: E402
from audit_questions import QuestionRequest, answer_question, report_findings  # noqa: E402
from evaluation import CASES_PATH, REPLAY_PATH, load_replay, load_suite, replay_report, validate_replay  # noqa: E402
from languages import Language, error_text  # noqa: E402
from parser import DocumentLimitError, ParseError, normalize_text, parse_document  # noqa: E402
from review_insights import insight_criteria  # noqa: E402
from schemas import CheckMode, DOCUMENT_ROLES, DocumentRole  # noqa: E402

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("auditor.api")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpx2").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

STATIC_DIR = BASE_DIR / "static"
TEMPLATE_DIR = BASE_DIR / "templates"


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, default)))
    except ValueError:
        return default


DEMO_PASSWORD = os.getenv("DEMO_PASSWORD", "")
MAX_SPEC_CHARS = _int_env("MAX_SPEC_CHARS", 60000)
MIN_SPEC_CHARS = _int_env("MIN_SPEC_CHARS", 40)
MAX_CONTEXT_CHARS = _int_env("MAX_CONTEXT_CHARS", 5000)
MAX_UPLOAD_MB = _int_env("MAX_UPLOAD_MB", 10)
MAX_BODY_BYTES = MAX_DOCUMENTS * MAX_UPLOAD_MB * 1024 * 1024 + 4 * (MAX_SPEC_CHARS + MAX_CONTEXT_CHARS) + 65536
AUDIT_PATHS = {"/api/audit/pdf", "/api/audit", "/api/audit/stream", "/api/audit/rules", "/api/audit/ask", "/api/audit/compare", "/api/document/inspect"}
NO_STORE = {"Cache-Control": "no-store"}
PACKAGE_INPUT = TypeAdapter(Annotated[list[PreparedDocument], Field(min_length=1, max_length=MAX_DOCUMENTS)])


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.settings = get_settings(live=False)
    app.state.llm_slots = asyncio.Semaphore(_int_env("GRAPH_RUNS", 1))
    app.state.demo = {}
    try:
        suite_path = BASE_DIR / (os.getenv("AUDIT_DEMO_DATASET") or CASES_PATH)
        replay_path = BASE_DIR / (os.getenv("AUDIT_DEMO_REPLAY") or REPLAY_PATH)
        suite, replay = load_suite(suite_path), load_replay(replay_path)
        if suite.demo:
            validate_replay(replay, suite, app.state.settings)
            cases = {case.id: case for case in suite.cases}
            app.state.demo = {
                language: (cases[pair.before], cases[pair.after], replay)
                for language, pair in suite.demo.items()
            }
    except DataFileError as exc:
        logger.info("Пример до и после недоступен (%s)", exc)
    yield


def _check_auth(x_demo_password: Optional[str]) -> None:
    if not DEMO_PASSWORD:
        return
    # Клиент передает пароль через encodeURIComponent, чтобы работала кириллица в заголовке.
    supplied = unquote(x_demo_password or "")
    if not secrets.compare_digest(supplied.encode("utf-8"), DEMO_PASSWORD.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Неверный пароль доступа")


class GuardAuditRequests:
    """Проверяет пароль и байтовый лимит до разбора формы FastAPI."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or scope["method"] != "POST"
                or scope["path"].rstrip("/") not in AUDIT_PATHS):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        header_language = headers.get("accept-language", "").split(",", 1)[0].split("-", 1)[0].lower()
        language = header_language if header_language in ("kk", "en") else "ru"
        try:
            _check_auth(headers.get("x-demo-password"))
            size = headers.get("content-length")
            if size is not None and (not size.isdecimal() or len(size) > 20):
                raise HTTPException(400, error_text(language, "body_size"))
            if size is not None and int(size) > MAX_BODY_BYTES:
                raise HTTPException(413, error_text(language, "body_limit"))
        except HTTPException as exc:
            await JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=NO_STORE)(scope, receive, send)
            return

        consumed = 0

        async def receive_limited():
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > MAX_BODY_BYTES:
                    raise HTTPException(413, error_text(language, "body_limit"))
            return message

        await self.app(scope, receive_limited, send)


app = FastAPI(title="REQUIRAGENTS API", lifespan=lifespan)
app.add_middleware(GuardAuditRequests)


@app.exception_handler(RequestValidationError)
async def invalid_request(request, exc):
    return JSONResponse({"detail": "Invalid request parameters."}, status_code=422, headers=NO_STORE)


cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
if cors_origins:
    app.add_middleware(CORSMiddleware, allow_origins=cors_origins, allow_methods=["*"], allow_headers=["*"])


async def _parse_upload(file: UploadFile, role: DocumentRole, language: Language, *, named_errors: bool) -> DocumentSource:
    name = safe_file_name(file.filename or "")
    limit = MAX_UPLOAD_MB * 1024 * 1024
    prefix = f"{name} · " if named_errors else ""
    try:
        raw = await file.read(limit + 1)
        if len(raw) > limit:
            raise HTTPException(413, prefix + error_text(language, "upload_limit", limit=MAX_UPLOAD_MB))
        parsed = await run_in_threadpool(parse_document, name, raw, max_chars=MAX_SPEC_CHARS, language=language)
        return DocumentSource(parsed.text, name, role, parsed.warnings)
    except HTTPException:
        raise
    except DocumentLimitError as exc:
        raise HTTPException(413, prefix + str(exc)) from None
    except ParseError as exc:
        raise HTTPException(400, prefix + str(exc)) from None
    except Exception as exc:
        logger.warning("Ошибка разбора документа (%s)", type(exc).__name__)
        raise HTTPException(400, prefix + error_text(language, "file_unreadable")) from None
    finally:
        await file.close()


async def _prepare_input(text: Optional[str], extra_context: Optional[str],
                         file: Optional[UploadFile], language: Language, *,
                         files: list[UploadFile] | None = None, roles: list[DocumentRole] | None = None,
                         package: str | None = None) -> tuple[DocumentBundle, str]:
    if (files and file) or (package and (files or file or (text or "").strip())):
        raise HTTPException(400, error_text(language, "document_selection"))
    uploads = files or ([file] if file and file.filename else [])
    if len(uploads) > MAX_DOCUMENTS:
        raise HTTPException(400, error_text(language, "document_count", limit=MAX_DOCUMENTS))
    if roles is not None and len(roles) != len(uploads):
        raise HTTPException(400, error_text(language, "document_roles"))

    if package:
        try:
            saved = PACKAGE_INPUT.validate_json(package)
        except ValidationError:
            raise HTTPException(400, error_text(language, "invalid_package")) from None
        sources = [DocumentSource(normalize_text(item.text), item.name, item.role) for item in saved]
    elif uploads:
        assignment = roles if roles is not None else ["main", *(["appendix"] * (len(uploads) - 1))]
        sources = []
        size = 0
        for upload, role in zip(uploads, assignment, strict=True):
            source = await _parse_upload(upload, role, language, named_errors=len(uploads) > 1)
            size += len(source.text) + (2 if sources else 0)
            if size > MAX_SPEC_CHARS:
                raise HTTPException(413, error_text(language, "text_limit", limit=MAX_SPEC_CHARS))
            sources.append(source)
    else:
        sources = [DocumentSource(normalize_text(text or ""))]
    if not any(source.text for source in sources):
        raise HTTPException(400, error_text(language, "missing_input"))
    if any(not source.text for source in sources):
        raise HTTPException(400, error_text(language, "invalid_package"))
    bundle = DocumentBundle(sources)
    spec_text = bundle.text
    if not spec_text:
        raise HTTPException(400, error_text(language, "missing_input"))
    if len(spec_text) < MIN_SPEC_CHARS:
        raise HTTPException(400, error_text(language, "short_text", limit=MIN_SPEC_CHARS))
    if len(spec_text) > MAX_SPEC_CHARS:
        raise HTTPException(413, error_text(language, "text_limit", limit=MAX_SPEC_CHARS))

    context = (extra_context or "").strip()
    if len(context) > MAX_CONTEXT_CHARS:
        raise HTTPException(413, error_text(language, "context_limit", limit=MAX_CONTEXT_CHARS))
    return bundle, context


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/config")
async def get_config(request: Request, language: Language = "ru"):
    settings = request.app.state.settings
    return {
        "auth_required": bool(DEMO_PASSWORD),
        "api_key_configured": bool(API_KEY and not DEMO_ONLY),
        "model": MODEL_NAME,
        "provider": "Gemini",
        "structured_mode": "schema",
        "agents": agent_list("deep", language),
        "check_modes": {mode: {"criteria": len(CRITERIA), "agents": agent_list(mode, language)}
                        for mode in ("quick", "deep")},
        "rule_modes": {mode: len(insight_criteria(settings.profile, mode)) for mode in ("quick", "deep")},
        "document_roles": list(DOCUMENT_ROLES),
        "profile": settings.profile.identity(language),
        "examples": [example.model_dump() for example in settings.profile.localized_examples(language)],
        "demo_available": language in request.app.state.demo,
        "limits": {
            "min_spec_chars": MIN_SPEC_CHARS,
            "max_spec_chars": MAX_SPEC_CHARS,
            "max_context_chars": MAX_CONTEXT_CHARS,
            "max_upload_mb": MAX_UPLOAD_MB,
            "max_files": MAX_DOCUMENTS,
            "max_defects_per_agent": settings.max_defects,
        },
    }


@app.post("/api/auth")
async def verify_auth(x_demo_password: Optional[str] = Header(default="")):
    _check_auth(x_demo_password)
    return {"status": "ok"}


async def _live_inputs(text, package, file, files, roles):
    uploads = files or ([file] if file and file.filename else [])
    if (files and file) or sum(map(bool, (uploads, package, text.strip()))) != 1:
        raise HTTPException(400, "Choose one document input method.")
    if len(uploads) > MAX_DOCUMENTS or (roles is not None and len(roles) != len(uploads)):
        raise HTTPException(400, "Choose between one and five documents with matching roles.")
    items = []
    if uploads:
        roles = roles or ["main", *(["appendix"] * (len(uploads) - 1))]
        for upload, role in zip(uploads, roles, strict=True):
            name = upload_name(upload.filename or "")
            raw = await upload.read(MAX_UPLOAD_MB * 1024 * 1024 + 1)
            if len(raw) > MAX_UPLOAD_MB * 1024 * 1024:
                raise HTTPException(413, "Upload size limit exceeded.")
            items.append((name, raw, role))
    elif package:
        try:
            documents = PACKAGE_INPUT.validate_json(package)
        except ValidationError:
            raise HTTPException(400, "Invalid revised document package.") from None
        for i, item in enumerate(documents, 1):
            # A revised PDF/DOCX is an edited text export, with new coordinates.
            name = Path(safe_file_name(item.name or f"document-{i:02d}")).with_suffix(".md").name
            items.append((upload_name(name), item.text.encode(), item.role))
    else:
        items.append(("specification.md", text.encode(), "main"))
    if len({name.casefold() for name, _, _ in items}) != len(items):
        raise HTTPException(400, "Document filenames must be unique.")
    return items


def _live_error(exc, language):
    if isinstance(exc, HTTPException):
        return exc.status_code, exc.detail
    cause = exc
    statuses = []
    while cause is not None:
        statuses.append(getattr(cause, "code", None))
        cause = cause.__cause__
    if 429 in statuses:
        return 429, "Gemini rate limit exceeded. Try again later."
    if 401 in statuses or 403 in statuses:
        return 502, {"ru": "Gemini отклонил запрос. Проверьте ключ и доступ.",
                     "en": "Gemini rejected the request. Check its key and access.",
                     "kk": "Gemini сұрауды қабылдамады. Кілт пен рұқсатты тексеріңіз."}[language]
    return 502, {"ru": "Не удалось завершить проверку. Повторите запуск или проверьте журнал сервера.",
                 "en": "The review did not complete. Retry or check the server log.",
                 "kk": "Тексеру аяқталмады. Қайталап көріңіз немесе сервер журналын қараңыз."}[language]


@app.post("/api/audit")
@app.post("/api/audit/stream")
async def audit_live(
    request: Request,
    text: str = Form(default=""),
    extra_context: str = Form(default=""),
    consent: bool = Form(default=False),
    cache: bool = Form(default=False),
    retry_token: str | None = Form(default=None, max_length=64),
    language: Language = Form(default="ru"),
    file: UploadFile | None = File(default=None),
    files: list[UploadFile] | None = File(default=None),
    roles: list[DocumentRole] | None = Form(default=None),
    mode: CheckMode = Form(default="deep"),
    package: str | None = Form(default=None),
):
    if not consent:
        raise HTTPException(403, "Explicit consent to send text to Gemini is required.")
    if DEMO_ONLY or not API_KEY:
        raise HTTPException(503, error_text(language, "model_unavailable"))
    if len(extra_context) > MAX_CONTEXT_CHARS:
        raise HTTPException(413, error_text(language, "context_limit", limit=MAX_CONTEXT_CHARS))
    context = extra_context.strip()
    if cache and context:
        raise HTTPException(400, "Persistent caching is unavailable with extra context.")
    if retry_token and (mode != "deep" or cache):
        raise HTTPException(400, "An in-memory Auditor retry requires Deep mode without disk caching.")
    items = await _live_inputs(text, package, file, files, roles)
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()
    disconnected = False
    started = False

    async def work(emit):
        nonlocal started
        async with request.app.state.llm_slots:
            started = True
            return await run_in_threadpool(analyze, items, request.app.state.settings, language, mode,
                                           context, MAX_SPEC_CHARS, emit, use_cache=cache, retry_token=retry_token)

    if not request.url.path.endswith("/stream"):
        try:
            return JSONResponse(await work(lambda event: None), headers=NO_STORE)
        except Exception as exc:
            status, message = _live_error(exc, language)
            raise HTTPException(status, message) from None

    async def events():
        nonlocal disconnected
        def publish(event):
            if not disconnected:
                queue.put_nowait(event)

        def emit(event):
            if not loop.is_closed():
                loop.call_soon_threadsafe(publish, event)

        async def run():
            try:
                queue.put_nowait({"type": "start", "agents": agent_list(mode, language)})
                report = await work(emit)
                publish({**report, "type": "result"})
            except Exception as exc:
                status, message = _live_error(exc, language)
                logger.warning("Graph analysis failed (%s)", type(exc).__name__)
                publish({"type": "error", "status": status, "detail": message})

        task = asyncio.create_task(run())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield "\n"
                    continue
                yield json.dumps(event, ensure_ascii=False) + "\n"
                if event["type"] in ("result", "error"):
                    return
        finally:
            # Once a worker starts, let it release its own temporary files and semaphore.
            # A disconnected client stops receiving events; it cannot interrupt an in-flight SDK call.
            disconnected = True
            if not started:
                task.cancel()

    return StreamingResponse(events(), media_type="application/x-ndjson",
                             headers={**NO_STORE, "X-Accel-Buffering": "no"})


def _document_summary(bundle: DocumentBundle) -> dict:
    requirements = bundle.requirements()
    sections = list(dict.fromkeys(item["section"] for item in requirements if item["section"]))
    return {
        "format": bundle.manifest[0]["format"] if len(bundle.sources) == 1 else "PACKAGE",
        "requirements": len(requirements), "section_count": len(sections), "sections": sections[:6],
        "warnings": bundle.warnings,
        **({"files": bundle.manifest} if len(bundle.sources) > 1 else {}),
    }


@app.post("/api/document/inspect")
async def inspect_document(
    text: Optional[str] = Form(default=""),
    language: Language = Form(default="ru"),
    file: Optional[UploadFile] = File(default=None),
    files: list[UploadFile] | None = File(default=None),
    roles: list[DocumentRole] | None = Form(default=None),
    package: str | None = Form(default=None),
    x_demo_password: Optional[str] = Header(default=""),
):
    _check_auth(x_demo_password)
    bundle, _ = await _prepare_input(text, "", file, language, files=files, roles=roles, package=package)
    return JSONResponse(_document_summary(bundle), headers=NO_STORE)


@app.post("/api/audit/compare")
async def compare_audits(comparison: ComparisonRequest, x_demo_password: Optional[str] = Header(default="")):
    _check_auth(x_demo_password)
    if sum(len(item.text) for item in comparison.after_sources) > MAX_SPEC_CHARS:
        raise HTTPException(413, error_text("ru", "text_limit", limit=MAX_SPEC_CHARS))
    return JSONResponse(
        compare_reports(comparison.before, comparison.after, comparison.after_sources), headers=NO_STORE,
    )


@app.post("/api/audit/rules")
async def audit_rules(
    request: Request,
    text: Optional[str] = Form(default=""),
    language: Language = Form(default="ru"),
    mode: CheckMode = Form(default="deep"),
    file: Optional[UploadFile] = File(default=None),
    files: list[UploadFile] | None = File(default=None),
    roles: list[DocumentRole] | None = Form(default=None),
    package: str | None = Form(default=None),
    x_demo_password: Optional[str] = Header(default=""),
):
    _check_auth(x_demo_password)
    bundle, _ = await _prepare_input(text, "", file, language, files=files, roles=roles, package=package)
    result = await run_in_threadpool(build_rules_report, bundle, request.app.state.settings, language, mode)
    return JSONResponse(result, headers=NO_STORE)


@app.get("/api/demo")
async def demo(request: Request, language: Language = "ru", mode: CheckMode = "deep",
               x_demo_password: Optional[str] = Header(default="")):
    _check_auth(x_demo_password)
    data = request.app.state.demo.get(language)
    if not data:
        raise HTTPException(404, "Для этого профиля пока нет примера до и после.")
    before, after, replay = data
    settings = request.app.state.settings
    results = [
        {
            "title": case.title,
            "text": case.text,
            "context": case.context,
            "report": replay_report(case, replay, settings, check_mode=mode),
        }
        for case in (before, after)
    ]
    return JSONResponse({"before": results[0], "after": results[1]}, headers=NO_STORE)


@app.get("/api/demo/pdf")
async def demo_pdf(request: Request, language: Language = "ru", mode: CheckMode = "deep",
                   version: Literal["before", "after"] = "before", executive: bool = True,
                   x_demo_password: Optional[str] = Header(default="")):
    _check_auth(x_demo_password)
    data = request.app.state.demo.get(language)
    if not data:
        raise HTTPException(404, "Demo unavailable.")
    before, after, replay = data
    case = before if version == "before" else after

    def render():
        from core.replay_pdf import render_replay_pdf
        report = replay_report(case, replay, request.app.state.settings, check_mode=mode)
        with TemporaryDirectory(prefix="requiragents-replay-") as folder:
            path = Path(folder) / "demo.pdf"
            render_replay_pdf(report, case.title, path, executive=executive)
            return path.read_bytes()

    return Response(await run_in_threadpool(render), media_type="application/pdf",
                    headers={**NO_STORE, "Content-Disposition": 'attachment; filename="requiragents-demo.pdf"'})


@app.post("/api/audit/ask")
async def ask_document(
    request: Request, question: QuestionRequest, x_demo_password: Optional[str] = Header(default=""),
):
    _check_auth(x_demo_password)
    settings = request.app.state.settings
    if question.demo_version:
        demo_data = request.app.state.demo.get(question.language)
        if not demo_data:
            raise HTTPException(404, error_text(question.language, "model_unavailable"))
        before, after, replay = demo_data
        case = before if question.demo_version == "before" else after
        demo_report = replay_report(case, replay, settings, check_mode=question.mode)
        result = await answer_question(question, demo_report["document"]["text"], report_findings(demo_report))
        return JSONResponse(result, headers=NO_STORE)

    bundle = None
    if question.documents:
        sources = [DocumentSource(normalize_text(item.text), item.name, item.role) for item in question.documents]
        if any(not item.text for item in sources):
            raise HTTPException(400, error_text(question.language, "invalid_package"))
        bundle = DocumentBundle(sources)
    document = bundle.text if bundle else normalize_text(question.document)
    if len(document) > MAX_SPEC_CHARS:
        raise HTTPException(413, error_text(question.language, "text_limit", limit=MAX_SPEC_CHARS))

    result = await answer_question(question, document, question.findings, bundle=bundle)
    return JSONResponse(result, headers=NO_STORE)


@app.post("/api/audit/pdf")
async def download_pdf(result: DeepRunResult | DeepPartialRunResult | QuickRunResult, executive: bool = False):
    finding_audit = getattr(result, "finding_audit", None)
    if executive and not isinstance(result, DeepRunResult) and finding_audit is None:
        raise HTTPException(422, "Executive report requires a completed audit.")
    def render():
        with TemporaryDirectory(prefix="requiragents-pdf-") as folder:
            path = Path(folder) / "report.pdf"
            if isinstance(result, DeepRunResult):
                validate_audit(result.audit, prepare_findings(result.quality, result.consistency), result.extraction)
            if finding_audit is not None:
                from core.quick_view import generate_quick_pdf as generate_partial_pdf
                validate_partial_audit(finding_audit, prepare_findings(result.quality, result.consistency), result.extraction)
                generate_partial_pdf(result, path, executive=executive)
                return path.read_bytes()
            generator = generate_pdf if isinstance(result, DeepRunResult) else generate_quick_pdf
            if executive:
                generate_base_pdf(result, path, executive=True)
            else:
                generator(result, path)
            return path.read_bytes()

    try:
        async with app.state.llm_slots:
            content = await run_in_threadpool(render)
    except Exception as exc:
        logger.warning("PDF export failed (%s)", type(exc).__name__)
        raise HTTPException(422, "Unable to export this report.") from None
    return Response(content, media_type="application/pdf",
                    headers={**NO_STORE, "Content-Disposition": 'attachment; filename="requiragents.pdf"'})


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _site_page(page: str, title: str) -> HTMLResponse:
    layout = (TEMPLATE_DIR / "layout.html").read_text(encoding="utf-8")
    content = (TEMPLATE_DIR / f"{page}.html").read_text(encoding="utf-8")
    html = (
        layout.replace("{{ title }}", title)
        .replace("{{ page }}", page)
        .replace("<!-- PAGE CONTENT -->", content)
    )
    return HTMLResponse(
        html,
        headers={
            "Cache-Control": "no-cache",
            "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/", response_class=HTMLResponse)
def index():
    return _site_page("home", "REQUIRAGENTS · проверка ТЗ до разработки")


@app.get("/audit", response_class=HTMLResponse)
def audit_page():
    return _site_page("audit", "Проверка ТЗ · REQUIRAGENTS")


@app.get("/how", response_class=HTMLResponse)
def how_page():
    return _site_page("how", "Как это работает · REQUIRAGENTS")
