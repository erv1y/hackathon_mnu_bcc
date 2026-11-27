import argparse
import hashlib
import json
import logging
import os
import time
from collections import Counter
from contextlib import nullcontext
from dataclasses import dataclass
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal, TypedDict
from uuid import uuid4

from langgraph.graph import StateGraph, START, END
from langgraph.types import Command
from langgraph.checkpoint.sqlite import SqliteSaver
from core.document_reader import read_document
from core.rule_engine import augment_consistency
from core.counterexample import analyze_counterexamples
from core.release_review import finalize_review
from core.models import (ValidationResult, ExtractionResult, QualityAnalysisResult,
                         ConsistencyAnalysisResult, AuditPatch, AuditResult)
from core.advanced_report import generate_pdf
from core.audit_metrics import audit_metrics, needs_human_review
from core.run_metadata import tracked, now, revision, id_mapping
from core.llm_client import MODEL_NAME, FALLBACK_MODELS, request_context, request_metrics, request_retry, request_language, request_stage, request_deadline
from agents.quick_validator import quick_validate, QuickValidationResult
from agents.extractor import extract_requirements
from agents.quality_analyst import analyze_quality
from agents.consistency_analyst import analyze_consistency, validate_findings
from agents.auditor import (
    audit_progress, audit_budget, AUDIT_SECONDS, MAX_AUDIT_REQUESTS, salvage_decisions,
    review_global_groups, apply_readiness_floor, AuditorFailure, AuditValidationError, audit_batch_size, audit_models, audit_results, audit_trace,
    prepare_findings, validate_assessments, validate_audit, validate_source_registry,
)

BASE_DIR = Path(__file__).resolve().parent
REPORTS_DIR = BASE_DIR / 'reports'
CACHE_DIR = BASE_DIR / '.validator_cache'
CACHE_VERSION = '13'
logger = logging.getLogger(__name__)

class DeepRunResult(ValidationResult):
    passport: dict

class QuickRunResult(QuickValidationResult):
    passport: dict
    mode: Literal['quick'] = 'quick'
    source_files: list[str]
    execution_time_seconds: float
    independent_audit: bool = False

class DeepPartialRunResult(QuickValidationResult):
    passport: dict
    mode: Literal["deep"] = "deep"
    source_files: list[str]
    execution_time_seconds: float
    independent_audit: Literal[False] = False
    finding_audit: AuditPatch | None = None
    audit_coverage: dict | None = None
    auditor_failure: Literal["auditor_validation_failed", "auditor_provider_failed", "consistency_incomplete", "source_coverage_incomplete", "auditor_budget_exhausted"]


class ValidationState(TypedDict, total=False):
    audit_checkpoint: dict
    audit_failure_code: str | None
    mode: str
    source_files: list[str]
    sections: list[dict]
    extraction: dict
    quality: dict
    consistency: dict
    audit: dict
    extraction_models: list[dict]
    quality_models: list[dict]
    consistency_models: list[dict]
    logic_debug: dict
    audit_models: list[dict]
    release_review_debug: dict
    quick_models: list[dict]
    original_started: str
    original_revision: dict

@dataclass(frozen=True)
class ProgressEvent:
    percent: int
    stage: str
    message: str


def emit_progress(callback, percent, stage, message):
    if callback is not None:
        callback(ProgressEvent(percent, stage, message))


def save_json(result, filename, reports_dir=REPORTS_DIR):
    folder = Path(reports_dir)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / filename
    temp = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=folder,
                                delete=False) as handle:
            temp = Path(handle.name)
            handle.write(result.model_dump_json(indent=2))
        os.replace(temp, target)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
    print(f'[SAVED] {filename}')
    return target


def normalize_paths(file_path):
    paths = [Path(file_path)] if isinstance(file_path, (str, os.PathLike)) else [Path(p) for p in file_path]
    if not paths or len(paths) > 5:
        raise ValueError('Choose between 1 and 5 documents.')
    if len({p.name.casefold() for p in paths}) != len(paths):
        raise ValueError('Document filenames must be unique.')
    return sorted(paths, key=lambda p: p.name.casefold())


def document_key(file_path, mode="deep", context="", language="ru"):
    paths = normalize_paths(file_path)
    digest = hashlib.sha256()
    def add(value):
        digest.update(len(value).to_bytes(8, 'big'))
        digest.update(value)
    add(CACHE_VERSION.encode())
    add(mode.encode())
    add(context.encode())
    add(language.encode())
    for path in paths:
        add(path.name.encode('utf-8'))
        add(path.read_bytes())
    add(json.dumps([MODEL_NAME, FALLBACK_MODELS, audit_models(), audit_batch_size()]).encode())
    sources = [BASE_DIR / 'orchestrator.py']
    sources += [BASE_DIR / 'core' / name for name in (
        'analytical_policy.py', 'models.py', 'document_reader.py', 'llm_client.py', 'run_metadata.py', 'rule_engine.py', 'business_logic.py', 'counterexample.py', 'release_review.py')]
    sources += [BASE_DIR / 'agents' / (name + '.py') for name in (
        'extractor', 'quality_analyst', 'consistency_analyst', 'auditor', 'quick_validator')]
    sources += sorted((BASE_DIR / 'prompts').glob('*.txt'))
    for source in sources:
        add(source.relative_to(BASE_DIR).as_posix().encode())
        add(source.read_bytes())
    for package in ('langgraph', 'langgraph-checkpoint-sqlite', 'google-genai',
                    'pydantic', 'pymupdf', 'python-docx'):
        try:
            add(f'{package}={version(package)}'.encode())
        except PackageNotFoundError:
            add(f'{package}=unknown'.encode())
    return digest.hexdigest()


def validate_extraction(sections, extraction):
    ids = [r.id for r in extraction.requirements]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError('Empty extraction or duplicate requirement IDs.')
    for r in extraction.requirements:
        quote = ' '.join(r.text.split())
        if not quote or not any(
            s['source'] == r.source and s.get('page') == r.page
            and s.get('paragraph') == r.paragraph
            and quote in ' '.join(s['text'].split()) for s in sections
        ):
            raise ValueError(f'Unsupported extracted requirement: {r.id}')
    print('[VALIDATOR] Extraction evidence passed')


def normalize_audit(quality, consistency, audit):
    originals = {f['id']: f for f in prepare_findings(quality, consistency)}
    for d in audit.decisions:
        if d.decision == 'confirmed' and d.final_severity != originals[d.finding_id]['severity']:
            d.decision = 'revised'
        original = originals[d.finding_id]
        d.recommendation = d.recommendation.removeprefix("REWRITE:").strip()
        if original.get("category") in {"contradiction", "inconsistent_constraint", "scope_conflict"}:
            d.action_kind = "business_decision"
        if d.action_kind == "business_decision":
            refs = ", ".join(d.requirement_ids)
            templates = {
                "ru": "Владелец процесса выбирает применимое правило для {refs} по вопросу «{title}». Сопоставьте условия и исключения в цитатах. В критериях приёмки закрепите один ожидаемый результат для каждого спорного и граничного случая.",
                "en": "The process owner should resolve ‘{title}’ for {refs}. Compare the conditions and exceptions in the quoted rules. Record one approved outcome for each disputed and boundary case in acceptance tests.",
                "kk": "Процесс иесі {refs} үшін «{title}» мәселесін шешуі керек. Дәйексөздердегі шарттар мен ерекшеліктерді салыстырыңыз. Әр даулы және шекаралық жағдай үшін мақұлданған нәтижені қабылдау сынағында бекітіңіз.",
            }
            proposal = {
                "ru": "Предложение для проверки, требует утверждения",
                "en": "Proposal for review, approval required",
                "kk": "Тексеруге арналған ұсыныс, мақұлдау қажет",
            }[request_language.get()]
            d.recommendation = (f"{proposal}. {d.recommendation} "
                                + templates[request_language.get()].format(refs=refs, title=d.title))
        if d.final_severity in ("high", "critical") and not d.severity_rationale.strip():
            d.severity_rationale = d.audit_reason
        if d.action_kind == "rewrite":
            # Permit whitespace-only rewrites; any substantive edit needs explicit review.
            if len(d.final_evidence) != 1 or d.recommendation.split() != d.final_evidence[0].quote.split():
                d.action_kind = "clarify"
    impact = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    retained = sorted((d for d in audit.decisions if d.decision in ("confirmed", "revised") and not needs_human_review(d)),
                      key=lambda d: (impact[d.final_severity], d.action_kind != "business_decision", d.finding_id))
    if isinstance(audit, AuditResult):
        audit.priority_actions = list(dict.fromkeys(d.recommendation for d in retained))[:8]
    return audit


def timed_analysis(name, function, *args):
    started = time.perf_counter()
    try:
        return function(*args)
    finally:
        print(f'[PERFORMANCE] {name}: {time.perf_counter()-started:.2f} seconds')


def extractor_node(state):
    print('[GRAPH NODE 1] Extractor')
    models = []
    if len(state['source_files']) == 1:
        result, models = tracked(timed_analysis, 'Extractor', extract_requirements, state['sections'])
    else:
        combined = []
        for index, source in enumerate(state['source_files'], 1):
            sections = [s for s in state['sections'] if s['source'] == source]
            print(f'[DOCUMENT {index}]')
            local, calls = tracked(timed_analysis, f'Extractor D{index:02d}', extract_requirements, sections)
            models.extend(dict(call, source=source) for call in calls)
            validate_extraction(sections, local)
            for requirement in local.requirements:
                combined.append(requirement.model_copy(update={'id': f'D{index:02d}::{requirement.id}'}))
        result = ExtractionResult(requirements=combined)
    validate_extraction(state['sections'], result)
    return {'extraction': result.model_dump(mode='json'), 'extraction_models': models}


def quality_node(state):
    print('[GRAPH NODE 2] Quality Analyst')
    extraction = ExtractionResult.model_validate(state['extraction'])
    result, models = tracked(timed_analysis, 'Quality Analyst', analyze_quality, extraction, state['sections'])
    return {'quality': result.model_dump(mode='json'), 'quality_models': models}


def consistency_node(state):
    print('[GRAPH NODE 3] Consistency Analyst')
    extraction = ExtractionResult.model_validate(state['extraction'])
    result, models = tracked(timed_analysis, 'Consistency Analyst', analyze_consistency, extraction, state['sections'])
    result = augment_consistency(extraction, result, state['sections'])
    logic_debug = {}
    token = request_stage.set('counterexample')
    started = time.perf_counter()
    try:
        result, logic_models = tracked(timed_analysis, 'Direct Counterexample Engine', analyze_counterexamples, extraction, result, state['sections'], logic_debug)
    finally:
        request_stage.reset(token)
    logic_debug['elapsed_seconds'] = round(time.perf_counter() - started, 3)
    models.extend(dict(call, stage='direct_counterexample') for call in logic_models)
    validate_findings(result, extraction)
    return {'consistency': result.model_dump(mode='json'), 'consistency_models': models, 'logic_debug': logic_debug}


def auditor_node(state):
    print('[GRAPH NODE 4] Auditor')
    extraction = ExtractionResult.model_validate(state['extraction'])
    quality = QualityAnalysisResult.model_validate(state['quality'])
    consistency = ConsistencyAnalysisResult.model_validate(state['consistency'])
    progress = dict(state.get("audit_checkpoint", {}))
    token = audit_progress.set(progress)
    deadline = time.monotonic() + AUDIT_SECONDS
    budget_token = audit_budget.set({"remaining": MAX_AUDIT_REQUESTS, "deadline": deadline})
    deadline_token = request_deadline.set(deadline)
    try:
        return run_auditor(state, extraction, quality, consistency, progress)
    finally:
        audit_progress.reset(token)
        audit_budget.reset(budget_token)
        request_deadline.reset(deadline_token)


def run_auditor(state, extraction, quality, consistency, progress):
    try:
        result, models = tracked(timed_analysis, 'Auditor 2.0', audit_results, state['sections'],
                                 extraction, quality, consistency, state.get('logic_debug'))
    except AuditorFailure as error:
        return {"audit_checkpoint": progress, "audit_failure_code": error.code}
    original_consistency = consistency.model_copy(deep=True)
    original_audit = result.model_dump(mode='json')
    try:
        consistency, result, review_debug = finalize_review(extraction, consistency, result, state['sections'])
        validate_findings(consistency, extraction)
        findings = prepare_findings(quality, consistency)
        validate_audit(result, findings, extraction)
        if new_ids := set(review_debug["added_review_ids"]):
            result, review_models = tracked(review_global_groups, result, findings, extraction, new_ids)
            models.extend(review_models)
        apply_readiness_floor(result, state.get('logic_debug'))
    except (ValueError, AuditorFailure) as error:
        logger.warning("Auditor post-review failed (%s)", type(error).__name__)
        base = AuditResult.model_validate(original_audit)
        validate_audit(base, prepare_findings(quality, original_consistency), extraction)
        progress.update(
            decisions=base.model_dump(mode="json")["decisions"],
            summary=base.model_dump(mode="json", exclude={"decisions"}),
            deduplication_complete=True, post_review_complete=False,
        )
        return {"audit_checkpoint": progress, "audit_failure_code":
                error.code if isinstance(error, AuditorFailure) else "auditor_validation_failed",
                "audit_models": models}
    progress["post_review_complete"] = True
    review_debug['pre_review_audit'] = original_audit
    return {'consistency': consistency.model_dump(mode='json'), 'audit': normalize_audit(quality, consistency, result).model_dump(mode='json'), 'audit_models': models, 'release_review_debug': review_debug,
            'audit_checkpoint': progress, 'audit_failure_code': None}


def quick_node(state):
    result, models = tracked(timed_analysis, 'Quick Agent', quick_validate, state['sections'])
    return dict(result.model_dump(mode='json'), quick_models=models)


def choose_mode(state):
    return 'quick' if state['mode'] == 'quick' else 'extractor'


def build_graph(checkpointer=None, node_callback=None, context="", metrics=None, language="ru", timings=None):
    def observed(name, fn):
        if node_callback is None and not context and metrics is None and language == "ru":
            return fn

        def execute(state):
            started = time.perf_counter()
            stage_token = request_stage.set(name)
            language_token = request_language.set(language)
            token = request_context.set(context)
            metric_token = request_metrics.set(metrics)
            retry_token = request_retry.set((lambda: node_callback(name, "retrying")) if node_callback else None)
            try:
                if node_callback:
                    node_callback(name, "started")
                result = fn(state)
                if node_callback:
                    status = "consistency_incomplete" if name == "consistency" and result["consistency"].get("unresolved") else "completed"
                    node_callback(name, result.get("audit_failure_code") or status)
                return result
            except AuditorFailure as error:
                if node_callback:
                    node_callback(name, error.code)
                raise
            except Exception:
                if node_callback:
                    node_callback(name, "failed")
                raise
            finally:
                if timings is not None:
                    timings[name] = round(time.perf_counter() - started, 3)
                request_stage.reset(stage_token)
                request_language.reset(language_token)
                request_retry.reset(retry_token)
                request_metrics.reset(metric_token)
                request_context.reset(token)

        return execute

    builder = StateGraph(ValidationState)
    for name, fn in [('extractor', extractor_node), ('quality', quality_node),
                     ('consistency', consistency_node), ('auditor', auditor_node)]:
        builder.add_node(name, observed(name, fn))
    builder.add_node('quick', observed('quick', quick_node))
    builder.add_conditional_edges(START, choose_mode, {'quick': 'quick', 'extractor': 'extractor'})
    builder.add_edge('quick', END)
    builder.add_edge('extractor', 'quality')
    builder.add_edge('extractor', 'consistency')
    builder.add_edge(['quality', 'consistency'], 'auditor')
    builder.add_edge('auditor', END)
    return builder.compile(checkpointer=checkpointer)


def result_from_state(state, elapsed, passport):
    extraction = ExtractionResult.model_validate(state['extraction'])
    quality = QualityAnalysisResult.model_validate(state['quality'])
    consistency = ConsistencyAnalysisResult.model_validate(state['consistency'])
    audit = AuditResult.model_validate(state['audit'])
    validate_extraction(state['sections'], extraction)
    if Counter(r.id for r in extraction.requirements) != Counter(a.requirement_id for a in quality.assessments):
        raise ValueError('Invalid quality assessment IDs.')
    validate_findings(consistency, extraction)
    validate_audit(audit, prepare_findings(quality, consistency), extraction)
    return DeepRunResult(passport=passport, source_files=state['source_files'], execution_time_seconds=round(elapsed, 2),
                            extraction=extraction, quality=quality, consistency=consistency, audit=audit)


def export_result(result, folder):
    for filename, data in [('extracted_requirements.json', result.extraction),
                           ('quality_analysis.json', result.quality),
                           ('consistency_analysis.json', result.consistency),
                           ('audit_results.json', result.audit),
                           ('final_validation.json', result)]:
        save_json(data, filename, folder)


def run_validation(file_path, progress_callback=None, reports_dir=None,
                   use_cache=False, force_reanalysis=False, mode="deep", *,
                   node_callback=None, sections=None, context="", write_reports=True, memory_checkpoint=None, language="ru"):
    if mode not in ("quick", "deep"):
        raise ValueError("Invalid validation mode")
    started = time.perf_counter()
    paths = normalize_paths(file_path)
    folder = Path(reports_dir) if reports_dir is not None else REPORTS_DIR
    emit_progress(progress_callback, 5, 'Document Reader', 'Чтение документа...')
    if sections is None:
        sections = [section for path in paths for section in read_document(path)]
    initial = {'mode': mode, 'source_files': [path.name for path in paths], 'sections': sections, 'original_started': now(), 'original_revision': revision()}
    print(f'[DOCUMENTS] {len(paths)}')
    print('[ENGINE] LangGraph StateGraph')
    # Without consent, checkpoint state exists only in RAM.
    if use_cache and memory_checkpoint is not None:
        raise ValueError("Choose one checkpoint store.")
    if use_cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        key = document_key(paths, mode, context, language)
        database = str(CACHE_DIR / f'{key}.sqlite')
    elif memory_checkpoint is not None:
        key = 'run'
    else:
        database = ':memory:'
        key = uuid4().hex
    config = {'configurable': {'thread_id': key}}
    store = nullcontext(memory_checkpoint) if memory_checkpoint is not None else SqliteSaver.from_conn_string(database)
    audit_failure = None
    with store as saver:
        metrics = []
        timings = {}
        graph = build_graph(saver, node_callback, context, metrics, language, timings)
        if force_reanalysis and use_cache:
            # Delete only the current document/configuration thread.
            saver.delete_thread(key)
        snapshot = graph.get_state(config)
        state = dict(snapshot.values or {})
        required = ('extraction', 'quality', 'consistency') + (('audit',) if mode == 'deep' else ())
        complete = all(k in state for k in required)
        run_kind = 'new'
        if complete and not snapshot.next:
            run_kind = 'cache_hit'
            print('[CACHE HIT] No AI calls')
            emit_progress(progress_callback, 90, 'Cache Hit', 'Готовый анализ восстановлен из кэша.')
        else:
            restart_auditor = mode == "deep" and bool(state.get("audit_failure_code")) and not snapshot.next
            resume = bool(snapshot.next) or restart_auditor
            if resume:
                run_kind = 'resume'
                print(f'[RESUME] Pending nodes: {snapshot.next}')
                emit_progress(progress_callback, 10, 'Resume', 'Восстановление сохранённых этапов...')
                if node_callback and mode == 'deep':
                    for node, field in (('extractor', 'extraction'), ('quality', 'quality'), ('consistency', 'consistency')):
                        if field in state and node not in snapshot.next:
                            node_callback(node, "reused")
            else:
                print('[NEW RUN]')
                state = dict(initial)
            completed = {name for name in ('quality', 'consistency') if name in state}
            def progress():
                if mode == 'quick':
                    emit_progress(progress_callback, 85 if 'consistency' in state else 15, 'Quick Agent', 'Предварительная проверка одним агентом...')
                elif 'audit' in state:
                    emit_progress(progress_callback, 85, 'Auditor Complete', 'Аудит завершён.')
                elif len(completed) == 2:
                    emit_progress(progress_callback, 75, 'Auditor', 'Agent 4 перепроверяет замечания...')
                elif len(completed) == 1:
                    name = 'Quality Analyst' if 'quality' in completed else 'Consistency Analyst'
                    emit_progress(progress_callback, 55, 'Parallel Analysis', f'{name} завершил работу. Ожидаем второго агента...')
                elif 'extraction' in state:
                    emit_progress(progress_callback, 35, 'Parallel Analysis', 'Agent 2 и Agent 3 анализируют требования...')
                else:
                    emit_progress(progress_callback, 15, 'Extractor', 'Agent 1 извлекает требования...')
            progress()
            try:
                graph_input = Command(goto="auditor") if restart_auditor else None if resume else initial
                for update in graph.stream(graph_input, config, stream_mode='updates'):
                    for node, payload in update.items():
                        if node not in ('extractor', 'quality', 'consistency', 'auditor', 'quick') or not payload:
                            continue
                        state.update(payload)
                        if node in ('quality', 'consistency'):
                            completed.add(node)
                        progress()
            except AuditorFailure as error:
                audit_failure = error
            snapshot = graph.get_state(config)
            state = dict(snapshot.values)
            if audit_failure and (mode != 'deep' or snapshot.next != ('auditor',)):
                raise audit_failure
            if state.get('audit_failure_code') and audit_failure is None:
                audit_failure = AuditorFailure(state['audit_failure_code'])
            if snapshot.next and not audit_failure:
                raise RuntimeError('Graph did not finish; retry to resume.')
        passport = {'mode': mode, 'run_kind': run_kind, 'analysis_started_utc': state.get('original_started'), 'report_run_utc': now(), 'analysis_revision': state.get('original_revision'), 'current_revision': revision(), 'configured_model': MODEL_NAME, 'successful_responses': {k: state.get(k+'_models', []) for k in (['quick'] if mode == 'quick' else ['extraction','quality','consistency','audit'])}}
        passport.update(language=language, stage_seconds=timings, model_attempts=metrics)
        passport["api_attempts"] = len(metrics)
        if "consistency" in timings and "elapsed_seconds" in state.get("logic_debug", {}):
            timings["counterexample"] = state["logic_debug"]["elapsed_seconds"]
            timings["consistency"] = round(max(0, timings["consistency"] - timings["counterexample"]), 3)
        passport["reported_tokens"] = {
            field: sum(values) if (values := [call[field] for call in metrics if call.get(field) is not None]) else None
            for field in ("input_tokens", "output_tokens", "total_tokens")
        }
        if mode == 'quick':
            result = QuickRunResult(passport=passport, source_files=state['source_files'],
                execution_time_seconds=round(time.perf_counter()-started, 2),
                extraction=state['extraction'], quality=state['quality'], consistency=state['consistency'])
            validate_extraction(sections, result.extraction)
            validate_findings(result.consistency, result.extraction)
            if Counter(r.id for r in result.extraction.requirements) != Counter(a.requirement_id for a in result.quality.assessments):
                raise ValueError('Invalid quick assessment IDs')
        else:
            passport['logic_coverage'] = {k: v for k, v in state.get('logic_debug', {}).items() if k != 'passes'}
            passport['release_review'] = {k:v for k,v in state.get('release_review_debug', {}).items() if k != 'pre_review_audit'}
            if audit_failure:
                extraction = ExtractionResult.model_validate(state['extraction'])
                quality = QualityAnalysisResult.model_validate(state['quality'])
                consistency = ConsistencyAnalysisResult.model_validate(state['consistency'])
                validate_assessments(quality, validate_source_registry(sections, extraction))
                validate_findings(consistency, extraction)
                all_findings = prepare_findings(quality, consistency)
                checkpoint = state.get("audit_checkpoint", {})
                stored = AuditPatch.model_validate({"decisions": checkpoint.get("decisions", [])})
                patch = salvage_decisions(stored.decisions, all_findings, extraction)
                token = request_language.set(language)
                try:
                    patch = normalize_audit(quality, consistency, patch)
                finally:
                    request_language.reset(token)
                audited_ids = {row.finding_id for row in patch.decisions}
                missing_ids = {row["id"] for row in all_findings} - audited_ids
                reviewed_ids = set(checkpoint.get("reviewed_ids", []))
                coverage = {
                    "validated_count": len(audited_ids), "unresolved_ids": sorted(missing_ids & reviewed_ids),
                    "not_reviewed_ids": sorted(missing_ids - reviewed_ids),
                    "completed_batches": checkpoint.get("completed_batches", []),
                    "incomplete_batches": checkpoint.get("incomplete_batches", []),
                    "summary_validated": checkpoint.get("summary") is not None,
                    "deduplication_complete": checkpoint.get("deduplication_complete", False),
                    "post_review_complete": False, "document_coverage_complete": False,
                    "readiness_eligible": False,
                    "rejected_ids": [d.finding_id for d in patch.decisions if d.decision == "rejected"],
                    "merged_ids": [d.finding_id for d in patch.decisions if d.decision == "merged"],
                }
                result = DeepPartialRunResult(passport=passport, source_files=state['source_files'],
                    finding_audit=patch if patch.decisions else None, audit_coverage=coverage,
                    execution_time_seconds=round(time.perf_counter() - started, 2), extraction=extraction,
                    quality=quality, consistency=consistency, auditor_failure=audit_failure.code)
            else:
                result = result_from_state(state, time.perf_counter() - started, passport)
                checkpoint = state.get("audit_checkpoint", {})
                coverage = {
                    "validated_count": len(result.audit.decisions), "unresolved_ids": [],
                    "not_reviewed_ids": [], "completed_batches": checkpoint.get("completed_batches", []),
                    "incomplete_batches": [], "summary_validated": True,
                    "deduplication_complete": True, "post_review_complete": True,
                    "document_coverage_complete": not result.consistency.unresolved,
                    "readiness_eligible": not result.consistency.unresolved,
                    "rejected_ids": [d.finding_id for d in result.audit.decisions if d.decision == "rejected"],
                    "merged_ids": [d.finding_id for d in result.audit.decisions if d.decision == "merged"],
                }
                passport["audit_coverage"] = coverage
                if result.consistency.unresolved:
                    result = DeepPartialRunResult(
                        passport=passport, source_files=result.source_files,
                        execution_time_seconds=result.execution_time_seconds,
                        extraction=result.extraction, quality=result.quality,
                        consistency=result.consistency, auditor_failure="consistency_incomplete",
                        finding_audit=AuditPatch(decisions=result.audit.decisions), audit_coverage=coverage,
                    )
    if isinstance(result, DeepRunResult):
        result.passport["audit_metrics"] = audit_metrics(result)
    result.passport['id_mapping'] = id_mapping(result.extraction.requirements, sections)
    if write_reports and mode == 'quick':
        save_json(result, 'quick_validation.json', folder)
    elif write_reports and isinstance(result, DeepPartialRunResult):
        save_json(result, 'partial_validation.json', folder)
    elif write_reports:
        export_result(result, folder)
        (folder / 'release_review.json').write_text(json.dumps(state.get('release_review_debug', {}), ensure_ascii=False, indent=2), encoding='utf-8')
        (folder / "audit_trace_v08.json").write_text(json.dumps(audit_trace(result.audit, prepare_findings(result.quality, result.consistency)), ensure_ascii=False, indent=2), encoding="utf-8")
        if state.get('logic_debug'):
            folder.mkdir(parents=True, exist_ok=True)
            (folder / 'formalization_debug.json').write_text(json.dumps(state['logic_debug'], ensure_ascii=False, indent=2), encoding='utf-8')
    if isinstance(result, DeepPartialRunResult):
        emit_progress(progress_callback, 90, 'Auditor Failed', 'Независимый аудит не завершён.')
        logger.warning("Partial Deep report: %s", result.auditor_failure)
    else:
        emit_progress(progress_callback, 90, 'Validation Complete', 'Все результаты готовы.')
        print('[GRAPH] Execution completed')
    return result


def print_summary(result):
    if isinstance(result, (QuickRunResult, DeepPartialRunResult)):
        print('DEEP MODE — PARTIAL / INDEPENDENT AUDIT FAILED' if isinstance(result, DeepPartialRunResult)
              else 'QUICK MODE — PRELIMINARY / NO INDEPENDENT AUDIT')
        print(f'Requirements: {len(result.extraction.requirements)}')
        print(f'Quality issues: {sum(len(a.findings) for a in result.quality.assessments)}')
        print(f'Consistency issues: {len(result.consistency.findings)}')
        print(f'Execution time: {result.execution_time_seconds} seconds')
        return
    retained = [d for d in result.audit.decisions if d.decision in ('confirmed', 'revised')]
    counts = Counter(d.final_severity for d in retained)
    print('\nFINAL VALIDATION SUMMARY\nEngine: LangGraph')
    print(f'Requirements: {len(result.extraction.requirements)}')
    print(f'Quality issues: {sum(len(a.findings) for a in result.quality.assessments)}')
    print(f'Consistency issues: {len(result.consistency.findings)}')
    print(f'Final issues: {len(retained)}')
    for severity in ('critical', 'high', 'medium', 'low'):
        print(f'{severity.upper()}: {counts[severity]}')
    print(f'Readiness: {result.audit.readiness}')
    print(f'Execution time: {result.execution_time_seconds} seconds (current run only)')
    for n, action in enumerate(result.audit.priority_actions, 1):
        print(f'{n}. {action}')
    print('[COMPLETED] Validation finished')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--cache', action='store_true', help='Allow local persistent checkpoint storage')
    parser.add_argument('--force', action='store_true', help='Discard current cached run and start again')
    parser.add_argument('--input', nargs='+', type=Path, help='One to five documents')
    parser.add_argument('--mode', choices=['quick', 'deep'], default='deep')
    args = parser.parse_args()
    result = run_validation(args.input or [BASE_DIR / 'docs' / 'test.pdf'], use_cache=args.cache, force_reanalysis=args.force, mode=args.mode)
    print_summary(result)
    if isinstance(result, (QuickRunResult, DeepPartialRunResult)):
        from core.advanced_report import generate_quick_pdf
        generate_quick_pdf(result, REPORTS_DIR / ('partial_report.pdf' if isinstance(result, DeepPartialRunResult) else 'quick_report.pdf'))
    else:
        generate_pdf(result, REPORTS_DIR / 'final_report.pdf')
