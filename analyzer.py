"""
Параллельный FinTech-аудит ТЗ несколькими LLM-агентами.

Режимы получения структурированного ответа (LLM_STRUCTURED_MODE):
  * strict — OpenAI Structured Outputs (json_schema). Лучшее качество, но
             поддерживается не всеми провайдерами;
  * json   — response_format={"type": "json_object"} + схема в промпте;
  * text   — обычный ответ, JSON вытаскивается из текста (работает почти везде);
  * auto   — пробует strict -> json -> text и запоминает, что сработало.
"""

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional
from urllib.parse import urlparse

import openai
from anyio import CancelScope
from openai import AsyncOpenAI
from pydantic import ValidationError

from audit_documents import DocumentBundle, DocumentSource
from audit_profile import AuditProfile, load_profile
from document_review import document_review
from languages import REPORT_COPY, Language, error_text
from postprocess import SEV_RANK, refine
from prompts import JSON_FORMAT_HINT, build_system_prompt, build_user_prompt
from review_insights import build_insights
from schemas import SEVERITIES, AuditReport, CheckMode

logger = logging.getLogger("auditor.llm")

STRUCTURED_MODES = ("strict", "json", "text")

# Кэш: какой режим и нужна ли температура для пары (base_url, model).
_mode_cache: dict[tuple[str, str], str] = {}
_no_temperature: set[tuple[str, str]] = set()


class ConfigError(ValueError):
    """Ошибка конфигурации (.env), которую нужно показать пользователю."""


class LLMResponseError(RuntimeError):
    """Модель ответила, но ответ не удалось превратить в отчет."""


class LLMRefusalError(RuntimeError):
    """Провайдер отказался обрабатывать запрос."""


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(float(raw)))
    except (ValueError, OverflowError):
        logger.warning("Некорректное значение %s, используется %s", name, default)
        return default


def _env_float_or_none(name: str, default: Optional[float]) -> Optional[float]:
    raw = os.getenv(name)
    if raw is None:
        return default
    raw = raw.strip().lower()
    if raw in ("", "none", "off", "default"):
        return None
    try:
        return float(raw)
    except ValueError:
        logger.warning("Некорректное значение %s, используется %s", name, default)
        return default


def _is_placeholder_key(key: str) -> bool:
    return not key or "xxxx" in key.lower() or key.strip() in ("sk-...", "your-key", "changeme")


@dataclass
class Settings:
    api_key: str = field(repr=False)
    base_url: Optional[str] = field(repr=False)
    model: str
    temperature: Optional[float]
    timeout: int
    max_retries: int
    max_parallel: int
    structured_mode: str
    max_defects: int
    enabled_agents: list[str]
    profile: AuditProfile

    @property
    def provider(self) -> str:
        if not self.base_url:
            return "api.openai.com"
        return urlparse(self.base_url).hostname or "провайдер"

    @property
    def cache_key(self) -> tuple[str, str]:
        return (self.base_url or "openai", self.model)


def get_settings(profile: AuditProfile | None = None, *, live: bool = True) -> Settings:
    profile = profile or load_profile()
    base_url = ((os.getenv("OPENAI_BASE_URL") or "").strip().rstrip("/") or None) if live else None
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip() if live else ""
    # Локальные провайдеры (Ollama, LM Studio, vLLM) ключ не проверяют.
    if _is_placeholder_key(api_key) and base_url and urlparse(base_url).hostname in ("localhost", "127.0.0.1", "0.0.0.0"):
        api_key = "local-no-key"

    mode = (os.getenv("LLM_STRUCTURED_MODE") or "auto").strip().lower()
    if mode not in STRUCTURED_MODES + ("auto",):
        logger.warning("Неизвестный LLM_STRUCTURED_MODE=%r, используется auto", mode)
        mode = "auto"

    enabled_raw = [k.strip() for k in (os.getenv("ENABLED_AGENTS") or "").split(",") if k.strip()]
    enabled = list(dict.fromkeys(k for k in enabled_raw if k in profile.agents)) or list(profile.agents)
    unknown = [k for k in enabled_raw if k not in profile.agents]
    if unknown:
        logger.warning("ENABLED_AGENTS: есть неизвестные агенты (доступны: %s)", list(profile.agents))

    return Settings(
        api_key=api_key,
        base_url=base_url,
        model=(os.getenv("LLM_MODEL") or "gpt-4o-mini").strip() if live else "Сохранённый пример",
        temperature=_env_float_or_none("LLM_TEMPERATURE", 0.2),
        timeout=_env_int("LLM_TIMEOUT", 90),
        max_retries=_env_int("LLM_MAX_RETRIES", 1, minimum=0),
        max_parallel=_env_int("LLM_MAX_PARALLEL", 4),
        structured_mode=mode,
        max_defects=_env_int("MAX_DEFECTS_PER_AGENT", 8),
        enabled_agents=enabled,
        profile=profile,
    )


def is_llm_configured(settings: Optional[Settings] = None) -> bool:
    settings = settings or get_settings()
    return not _is_placeholder_key(settings.api_key)


MISSING_KEY_MESSAGE = (
    "Не задан OPENAI_API_KEY в файле .env (или оставлен шаблонный ключ из .env.example). "
    "После изменения .env перезапустите сервер."
)


def _get_client(settings: Settings) -> AsyncOpenAI:
    if not is_llm_configured(settings):
        raise ConfigError(MISSING_KEY_MESSAGE)
    return AsyncOpenAI(
        api_key=settings.api_key,
        base_url=settings.base_url,
        timeout=settings.timeout,
        max_retries=settings.max_retries,
    )


# ---------------------------------------------------------------- ошибки LLM

def _provider_message(exc: Exception) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if isinstance(err, str):
            return err
    return str(getattr(exc, "message", "") or exc)


def describe_llm_error(exc: BaseException, settings: Optional[Settings] = None,
                       language: Language = "ru") -> tuple[int, str]:
    """Возвращает безопасное сообщение без тела ответа провайдера."""
    if isinstance(exc, ConfigError):
        return 503, error_text(language, "model_unavailable")
    if isinstance(exc, (asyncio.TimeoutError, openai.APITimeoutError)):
        return 504, error_text(language, "model_timeout")
    if isinstance(exc, openai.RateLimitError):
        return 429, error_text(language, "model_limit")
    if isinstance(exc, (openai.ContentFilterFinishReasonError, LLMRefusalError)):
        return 502, error_text(language, "model_refusal")
    if isinstance(exc, (LLMResponseError, ValidationError, openai.LengthFinishReasonError)):
        return 502, error_text(language, "model_response")
    if isinstance(exc, openai.APIError):
        return 502, error_text(language, "model_unavailable")
    return 500, error_text(language, "model_unavailable")


# ------------------------------------------------------------ вызовы модели

def _extract_json(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        raise LLMResponseError("Модель вернула пустой ответ.")
    # Убираем <think>...</think> у reasoning-моделей и markdown-ограждения.
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start, end = text.find(open_ch), text.rfind(close_ch)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMResponseError("Модель вернула ответ не в формате JSON.")


def _report_from_payload(payload: Any) -> AuditReport:
    if isinstance(payload, list):
        payload = {"defects": payload}
    if not isinstance(payload, dict):
        raise LLMResponseError("Модель вернула JSON неожиданной структуры.")

    raw_defects = next((payload[k] for k in ("defects", "issues", "items") if k in payload), None)
    if not isinstance(raw_defects, list) or any(not isinstance(item, dict) for item in raw_defects):
        raise LLMResponseError("Модель вернула отчёт без корректного списка замечаний.")
    defects = [{str(k).strip().lower(): v for k, v in item.items()} for item in raw_defects]
    try:
        return AuditReport.model_validate({"defects": defects})
    except ValidationError:
        raise LLMResponseError("Модель вернула замечание без обязательных полей.") from None


def _parse_method(client: AsyncOpenAI):
    completions = client.chat.completions
    if hasattr(completions, "parse"):
        return completions.parse
    return client.beta.chat.completions.parse  # старые версии openai


def _completion_json(response) -> Any:
    if not response.choices:
        raise LLMResponseError("Провайдер вернул ответ без вариантов (choices).")
    choice = response.choices[0]
    if getattr(choice.message, "refusal", None) or choice.finish_reason == "content_filter":
        raise LLMRefusalError()
    if choice.finish_reason == "length":
        raise openai.LengthFinishReasonError(completion=response)
    return _extract_json(choice.message.content or "")


async def _call_mode(client: AsyncOpenAI, settings: Settings, mode: str, system: str, user: str) -> AuditReport:
    kwargs: dict[str, Any] = {"model": settings.model}
    if settings.temperature is not None and settings.cache_key not in _no_temperature:
        kwargs["temperature"] = settings.temperature

    if mode == "strict":
        response = await _parse_method(client)(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format=AuditReport,
            **kwargs,
        )
        if not response.choices:
            raise LLMResponseError("Модель вернула пустой ответ.")
        message = response.choices[0].message
        if getattr(message, "refusal", None):
            raise LLMRefusalError()
        if message.parsed is not None:
            return AuditReport.model_validate(message.parsed.model_dump())
        return _report_from_payload(_extract_json(message.content or ""))

    system_with_format = f"{system}\n\n{JSON_FORMAT_HINT}"
    if mode == "json":
        kwargs["response_format"] = {"type": "json_object"}
    response = await client.chat.completions.create(
        messages=[{"role": "system", "content": system_with_format}, {"role": "user", "content": user}],
        **kwargs,
    )
    return _report_from_payload(_completion_json(response))


def _is_temperature_error(exc: Exception) -> bool:
    return isinstance(exc, (openai.BadRequestError, openai.UnprocessableEntityError)) and "temperature" in _provider_message(exc).lower()


def _can_fallback(exc: Exception) -> bool:
    """Ошибки, при которых имеет смысл попробовать более простой режим."""
    if isinstance(exc, (openai.BadRequestError, openai.UnprocessableEntityError, openai.NotFoundError)):
        # 404 у некоторых прокси означает «нет такого параметра/эндпоинта», но если
        # модель не найдена — фоллбэк не поможет.
        if isinstance(exc, openai.NotFoundError) and "model" in _provider_message(exc).lower():
            return False
        return True
    return isinstance(exc, (LLMResponseError, ValidationError, openai.LengthFinishReasonError))


async def request_model_json(client: AsyncOpenAI, settings: Settings, system: str, payload: dict) -> Any:
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    if settings.structured_mode != "text":
        try:
            response = await client.chat.completions.create(
                model=settings.model, messages=messages, response_format={"type": "json_object"},
            )
            return _completion_json(response)
        except Exception as exc:
            if not _can_fallback(exc):
                raise
    response = await client.chat.completions.create(model=settings.model, messages=messages)
    return _completion_json(response)


async def _ask_llm(client: AsyncOpenAI, settings: Settings, system: str, user: str) -> AuditReport:
    if settings.structured_mode == "auto":
        start_mode = _mode_cache.get(settings.cache_key, "strict")
        modes = list(STRUCTURED_MODES[STRUCTURED_MODES.index(start_mode):])
    else:
        modes = [settings.structured_mode]

    for index, mode in enumerate(modes):
        try:
            report = await _call_mode_with_temperature_retry(client, settings, mode, system, user)
        except Exception as exc:
            if index == len(modes) - 1 or not _can_fallback(exc):
                raise
            logger.warning("Режим %s не сработал (%s), пробую следующий", mode, type(exc).__name__)
            continue
        if settings.structured_mode == "auto" and _mode_cache.get(settings.cache_key) != mode:
            _mode_cache[settings.cache_key] = mode
            logger.info("Режим структурированного ответа для %s: %s", settings.model, mode)
        return report
    raise LLMResponseError("Не удалось получить ответ модели ни в одном режиме.")


async def _call_mode_with_temperature_retry(client: AsyncOpenAI, settings: Settings, mode: str,
                                            system: str, user: str) -> AuditReport:
    try:
        return await _call_mode(client, settings, mode, system, user)
    except Exception as exc:
        # Reasoning-модели (o1/o3/gpt-5 и т.п.) принимают только temperature по умолчанию.
        if _is_temperature_error(exc) and settings.cache_key not in _no_temperature:
            _no_temperature.add(settings.cache_key)
            logger.info("Модель %s не принимает temperature — отправляем без него", settings.model)
            return await _call_mode(client, settings, mode, system, user)
        raise


# --------------------------------------------------------------- аудит

def get_active_agents(settings: Optional[Settings] = None, language: Language = "ru",
                      check_mode: CheckMode = "deep") -> list[dict]:
    settings = settings or get_settings()
    scope = settings.profile.check_criteria(check_mode)
    return [
        {
            "key": key,
            "title": settings.profile.agent_label(key, language).title,
            "scope": settings.profile.agent_label(key, language).scope,
            "style": settings.profile.agents[key].style,
            "criteria_count": sum(key in rule.agents for rule in scope.values()),
        }
        for key in settings.enabled_agents
        if any(key in rule.agents for rule in scope.values())
    ]


def _choose_findings(defects: list[dict], settings: Settings, check_mode: CheckMode) -> list[dict]:
    profile = settings.profile
    plan = profile.check_plan(check_mode)
    allowed = profile.check_criteria(check_mode)
    return [
        item for item in defects
        if SEV_RANK[item["severity"]] <= SEV_RANK[plan.minimum_severity]
        and (plan.criteria is None or item.get("criterion_id") in allowed)
    ][:min(settings.max_defects, plan.max_findings or settings.max_defects)]


async def _run_agent(client: AsyncOpenAI, settings: Settings, semaphore: asyncio.Semaphore,
                     agent_key: str, spec_text: str, extra_context: str, language: Language,
                     *, bundle: DocumentBundle | None = None, check_mode: CheckMode = "deep") -> dict:
    title = settings.profile.agent_label(agent_key, language).title
    started = time.perf_counter()
    try:
        async with semaphore:
            plan = settings.profile.check_plan(check_mode)
            limit = min(settings.max_defects, plan.max_findings or settings.max_defects)
            system = build_system_prompt(
                settings.profile, agent_key, limit, language, check_mode=check_mode,
                multi_file=bundle is not None and len(bundle.sources) > 1,
            )
            user = build_user_prompt(spec_text, extra_context, bundle=bundle)
            # Общий предохранитель поверх таймаута HTTP-клиента (с учетом ретраев и фоллбэков).
            hard_timeout = settings.timeout * (settings.max_retries + 1) + 15
            report = await asyncio.wait_for(_ask_llm(client, settings, system, user), timeout=hard_timeout)
        defects = _choose_findings([d.model_dump() for d in report.defects], settings, check_mode)
        elapsed = round(time.perf_counter() - started, 1)
        logger.info("Агент %s: %s замечаний за %.1f с", agent_key, len(defects), elapsed)
        return {"key": agent_key, "title": title, "ok": True, "defects": defects, "error": None,
                "status_code": 200, "elapsed_sec": elapsed}
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        status, message = describe_llm_error(exc, settings, language)
        elapsed = round(time.perf_counter() - started, 1)
        logger.warning("Агент %s завершился ошибкой за %.1f с (%s, %s)",
                       agent_key, elapsed, type(exc).__name__, getattr(exc, "status_code", status))
        return {"key": agent_key, "title": title, "ok": False, "defects": [], "error": message,
                "status_code": status, "elapsed_sec": elapsed}


def build_result(outcomes: dict[str, dict], settings: Settings, spec_text: str, elapsed: float,
                 *, source_warnings: tuple[str, ...] = (), mode: str = "live", language: Language = "ru",
                 bundle: DocumentBundle | None = None, check_mode: CheckMode = "deep") -> dict:
    """Собирает один и тот же отчёт для API, демо и оценки качества."""
    profile = settings.profile
    plan = profile.check_plan(check_mode)
    scope = profile.check_criteria(check_mode)
    active = [] if mode == "rules" else [agent["key"] for agent in get_active_agents(settings, language, check_mode)]
    bundle = bundle or DocumentBundle([DocumentSource(spec_text)])
    score_rules = profile.scoring
    # Порядок завершения запросов не влияет на принадлежность дублей к разделу.
    ordered = {
        key: {
            **outcomes[key], "title": profile.agent_label(key, language).title,
            "defects": _choose_findings([dict(d) for d in outcomes[key]["defects"]], settings, check_mode),
        }
        for key in active
    }
    merged = refine(ordered, spec_text, profile, language, bundle=bundle, check_mode=check_mode)
    counts = dict.fromkeys(SEVERITIES, 0)
    scored = dict.fromkeys(SEVERITIES, 0)
    modules, completed = [], set()
    pending = 0
    for key in active:
        outcome = ordered[key]
        if outcome["ok"]:
            completed.add(key)
        for defect in outcome["defects"]:
            severity = defect["severity"]
            counts[severity] += 1
            if not defect["criterion"]:
                reason = "unknown_criterion"
            elif defect["verified"] == "unverified":
                reason = defect["evidence_issue"]
            elif defect["verified"] == "missing" and not score_rules.count_missing:
                reason = "missing_excluded"
            else:
                reason = None
            defect["review_reason"] = reason
            defect["score_points"] = 0 if reason else score_rules.weights.get(severity, score_rules.weights["High"])
            if reason:
                pending += 1
            else:
                scored[severity] += 1
        modules.append({
            "key": key,
            "title": outcome["title"],
            "ok": outcome["ok"],
            "error": outcome["error"],
            "status_code": outcome["status_code"],
            "elapsed_sec": outcome["elapsed_sec"],
            "defects": outcome["defects"],
            "criteria": [
                {"id": criterion_id, "title": profile.criterion_title(criterion_id, language)}
                for criterion_id, rule in scope.items() if key in rule.agents
            ],
        })

    failed = [m for m in modules if not m["ok"]]
    penalty = sum(scored[sev] * score_rules.weights.get(sev, score_rules.weights["High"]) for sev in SEVERITIES)
    partial = bool(failed or source_warnings or not active)
    score = None if partial else max(0, 100 - penalty)
    if score is None:
        status = "incomplete"
    elif scored["Critical"] or scored["High"] or score < score_rules.warning_threshold:
        status = "critical"
    elif pending or score < score_rules.good_threshold:
        status = "review"
    else:
        status = "clear"
    covered = [key for key, rule in scope.items() if completed.intersection(rule.agents)]
    intended = [key for key, rule in scope.items() if set(active).intersection(rule.agents)]
    document = document_review(spec_text, modules, complete=not partial, bundle=bundle)

    return {
        "score": score,
        "score_status": status,
        "counts": counts,
        "scored_counts": scored,
        "modules": modules,
        "partial": partial,
        "failed_agents": [m["key"] for m in failed],
        "coverage": {
            "agents_completed": len(completed),
            "agents_total": len(modules),
            "criteria_in_completed_agents": len(covered),
            "criteria_total": len(intended),
        },
        "model": settings.model if mode == "live" else REPORT_COPY[language]["replay"],
        "provider": settings.provider if mode == "live" else REPORT_COPY[language]["local"],
        "mode": mode,
        "language": language,
        "profile": profile.identity(language),
        "report_format": profile.report_format(language),
        "spec_chars": len(spec_text),
        "source_warnings": list(source_warnings),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_sec": round(elapsed, 1),
        "merged_duplicates": merged,
        "pending_findings": pending,
        "unverified_quotes": sum(1 for m in modules for d in m["defects"] if d.get("verified") == "unverified"),
        "score_weights": score_rules.weights,
        "score_penalty": penalty,
        "score_formula": REPORT_COPY[language]["formula"],
        "document": document,
        "check": {
            "mode": check_mode, "source_hash": bundle.fingerprint, "criteria": intended,
            "completed_criteria": covered, "minimum_severity": plan.minimum_severity,
        },
        "insights": build_insights(
            bundle, [item for module in modules for item in module["defects"]], profile, check_mode, language,
        ),
    }


def build_rules_report(bundle: DocumentBundle, settings: Settings, language: Language, mode: CheckMode) -> dict:
    """Возвращает отчёт по программным правилам без оценки готовности модели."""
    start = time.perf_counter()
    report = build_result(
        {}, settings, bundle.text, 0, source_warnings=bundle.warnings,
        mode="rules", language=language, bundle=bundle, check_mode=mode,
    )
    insights = report["insights"]
    title = {"ru": "Проверка по правилам", "kk": "Ережелер бойынша тексеру", "en": "Rule-based review"}[language]
    rows = sorted([*insights["sanity"], *insights["ambiguities"]], key=lambda row: SEV_RANK[row["severity"]])
    findings = [
        {
            "title": row["title"], "severity": row["severity"], "criterion_id": row["criterion_id"],
            "criterion": {"id": row["criterion_id"], "title": row["title"]},
            "issue": row["question"], "clarification": row["question"], "suggestion": row["default"],
            "quote": row["evidence"][0]["text"], "evidence": row["evidence"], "verified": "exact",
            "score_points": 0, "review_reason": None, "also_by": [], "references": [], "fix_kind": "clarify",
            "insight_id": row["id"],
        }
        for row in rows
    ]
    criteria = insights["checked_criteria"]
    names = {item["criterion_id"]: item["title"] for item in rows}
    modules = [{
        "key": "rules", "title": title, "ok": True, "error": None, "status_code": 200,
        "elapsed_sec": round(time.perf_counter() - start, 2), "defects": findings,
        "criteria": [{"id": key, "title": names.get(key, key)} for key in criteria],
    }] if criteria else []
    report.update(
        model=title, partial=bool(bundle.warnings or not criteria), modules=modules,
        elapsed_sec=round(time.perf_counter() - start, 2),
        document=document_review(bundle.text, modules, complete=False, bundle=bundle),
        counts={level: sum(row["severity"] == level for row in rows) for level in SEVERITIES},
        coverage={
            "agents_completed": 0, "agents_total": 0,
            "criteria_in_completed_agents": len(criteria), "criteria_total": len(criteria),
        },
    )
    report["check"].update(criteria=criteria, completed_criteria=criteria, minimum_severity="Low")
    return report


async def audit_events(spec_text: str, extra_context: str = "", *,
                       settings: Settings | None = None, semaphore: asyncio.Semaphore | None = None,
                       source_warnings: tuple[str, ...] = (), language: Language = "ru",
                       bundle: DocumentBundle | None = None, check_mode: CheckMode = "deep") -> AsyncIterator[dict]:
    """Поток событий для параллельных проверок и сверки доказательств."""
    settings = settings or get_settings()
    agents = get_active_agents(settings, language, check_mode)
    if not agents:
        raise ConfigError("Для выбранного режима нет включённых агентов.")
    client = _get_client(settings)
    started = time.perf_counter()

    semaphore = semaphore or asyncio.Semaphore(settings.max_parallel)
    tasks = []
    outcomes: dict[str, dict] = {}
    try:
        yield {
            "type": "start",
            "agents": agents,
            "model": settings.model,
            "provider": settings.provider,
            "spec_chars": len(spec_text),
        }
        tasks = [
            asyncio.create_task(_run_agent(
                client, settings, semaphore, agent["key"], spec_text, extra_context, language,
                bundle=bundle, check_mode=check_mode,
            ))
            for agent in agents
        ]
        for next_done in asyncio.as_completed(tasks):
            outcome = await next_done
            outcomes[outcome["key"]] = outcome
            yield {"type": "agent_done", **outcome, "findings_count": len(outcome["defects"])}
    finally:
        with CancelScope(shield=True):
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await client.close()

    yield {"type": "validating"}
    yield {"type": "result", **build_result(outcomes, settings, spec_text, time.perf_counter() - started,
                                           source_warnings=source_warnings, language=language,
                                           bundle=bundle, check_mode=check_mode)}


async def run_full_fintech_audit(spec_text: str, extra_context: str = "", *,
                                 settings: Settings | None = None, semaphore: asyncio.Semaphore | None = None,
                                 source_warnings: tuple[str, ...] = (), language: Language = "ru",
                                 bundle: DocumentBundle | None = None, check_mode: CheckMode = "deep") -> dict:
    """Синхронный (одним ответом) вариант аудита — для /api/audit и скриптов."""
    result: dict = {}
    async for event in audit_events(spec_text, extra_context, settings=settings,
                                    semaphore=semaphore, source_warnings=source_warnings, language=language,
                                    bundle=bundle, check_mode=check_mode):
        if event["type"] == "result":
            result = {k: v for k, v in event.items() if k != "type"}
    return result
