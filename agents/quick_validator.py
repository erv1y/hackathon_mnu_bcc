"""Quick preliminary review. Source evidence is materialized from verified extraction."""
import json
import logging
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from agents.consistency_analyst import validate_findings
from agents.quality_analyst import validate_quality
from core.llm_client import ask_gemini, request_retry
from core.models import (
    ConsistencyAnalysisResult, ConsistencyFinding, ExtractionResult,
    QualityAnalysisResult, QualityFinding, RequirementAssessment, FindingEvidence,
)

BASE_DIR = Path(__file__).resolve().parent.parent
MAX_ATTEMPTS = 2
logger = logging.getLogger(__name__)


class QuickQualityFinding(BaseModel):
    criterion: Literal['correctness', 'clarity', 'completeness', 'verifiability',
                       'consistency', 'feasibility', 'necessity', 'traceability', 'unambiguity']
    severity: Literal['critical', 'high', 'medium', 'low']
    explanation: str
    severity_rationale: str = ''
    recommendation: str
    confidence: float = Field(ge=0, le=1)


class QuickAssessment(BaseModel):
    requirement_id: str
    status: Literal['passed', 'needs_revision', 'needs_clarification']
    quality_score: int = Field(ge=0, le=100)
    findings: list[QuickQualityFinding]


class QuickQualityResponse(BaseModel):
    assessments: list[QuickAssessment]


class QuickConsistencyFinding(BaseModel):
    category: Literal['contradiction', 'duplicate', 'missing_requirement',
                      'inconsistent_constraint', 'missing_dependency', 'scope_conflict',
                      'terminology_inconsistency']
    severity: Literal['critical', 'high', 'medium', 'low']
    requirement_ids: list[str] = Field(min_length=1)
    title: str
    explanation: str
    severity_rationale: str = ''
    impact: str
    recommendation: str
    confidence: float = Field(ge=0, le=1)


class QuickConsistencyResponse(BaseModel):
    findings: list[QuickConsistencyFinding]
    summary: str


class QuickAgentResponse(BaseModel):
    extraction: ExtractionResult
    quality: QuickQualityResponse
    consistency: QuickConsistencyResponse


class QuickRepairResponse(BaseModel):
    quality: QuickQualityResponse
    consistency: QuickConsistencyResponse | None = None


class QuickValidationResult(BaseModel):
    extraction: ExtractionResult
    quality: QualityAnalysisResult
    consistency: ConsistencyAnalysisResult


class QuickValidationError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def validate_extraction(extraction, sections, source_mapping):
    requirements = extraction.requirements
    ids = {requirement.id for requirement in requirements}
    if not ids or len(ids) != len(requirements) or any(not rid.strip() for rid in ids):
        raise QuickValidationError('invalid_extraction_ids')
    for requirement in requirements:
        quote = ' '.join(requirement.text.split())
        supported = quote and any(
            (section['source'], section.get('page'), section.get('paragraph'))
            == (requirement.source, requirement.page, requirement.paragraph)
            and quote in ' '.join(section['text'].split()) for section in sections
        )
        if not supported:
            raise QuickValidationError('unsupported_extraction_evidence')
        if len(source_mapping) > 1 and not requirement.id.startswith(source_mapping[requirement.source] + '::'):
            raise QuickValidationError('invalid_document_id')


def validate_quick_result(result, sections, source_mapping):
    validate_extraction(result.extraction, sections, source_mapping)
    try:
        validate_quality(result.quality, result.extraction)
    except ValueError:
        raise QuickValidationError('invalid_quality_ids_or_evidence') from None
    try:
        validate_findings(result.consistency, result.extraction)
    except ValueError:
        raise QuickValidationError('invalid_consistency_ids_or_evidence') from None


def quality_row(raw, requirement):
    # Old saved/mocked responses must still pass the original evidence validator.
    # Never silently replace a supplied invalid legacy quotation or wrong nested ID.
    if not isinstance(raw.get('findings'), list):
        raise ValueError('invalid_findings_array')
    findings = []
    for f in raw['findings']:
        if not isinstance(f, dict):
            raise ValueError('invalid_finding_schema')
        if 'evidence' in f or 'requirement_id' in f:
            row = QualityFinding.model_validate(f)
            if row.requirement_id != requirement.id:
                raise ValueError('finding_requirement_id_mismatch')
            quote = ' '.join(row.evidence.split())
            if not quote or quote not in ' '.join(requirement.text.split()):
                raise ValueError('invalid_quality_quotation')
        else:
            candidate = QuickQualityFinding.model_validate(f)
            row = QualityFinding(**candidate.model_dump(), requirement_id=requirement.id,
                                 evidence=requirement.text)
        findings.append(row)
    assessment = RequirementAssessment.model_validate({**raw, 'findings': findings})
    if (assessment.status == 'passed') != (not findings):
        raise ValueError('status_findings_mismatch')
    validate_quality(QualityAnalysisResult(assessments=[assessment]),
                     ExtractionResult(requirements=[requirement]))
    return assessment


def review_quality(raw, registry, retained):
    pending = set(registry) - retained.keys()
    reasons = {}
    rows = raw.get('assessments') if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        rows = []
    counts = Counter(row['requirement_id'] for row in rows if isinstance(row, dict)
                     and isinstance(row.get('requirement_id'), str))
    by_id = {row['requirement_id']: row for row in rows if isinstance(row, dict)
             and isinstance(row.get('requirement_id'), str)}
    for rid in sorted(pending):
        if counts[rid] != 1:
            reasons[rid] = 'missing_assessment' if not counts[rid] else 'duplicate_assessment'
            continue
        try:
            retained[rid] = quality_row(by_id[rid], registry[rid])
        except ValidationError:
            reasons[rid] = 'invalid_assessment_schema'
        except ValueError as error:
            safe = {'invalid_findings_array', 'invalid_finding_schema', 'finding_requirement_id_mismatch',
                    'invalid_quality_quotation', 'status_findings_mismatch'}
            reasons[rid] = str(error) if str(error) in safe else 'invalid_assessment'
    # Unsolicited rows cannot enter results. Missing IDs are never filled with fake passes.
    unexpected = sum(count for rid, count in counts.items() if rid not in registry)
    malformed = len(rows) - sum(counts.values())
    if unexpected or malformed:
        logger.warning('Quick quality ignored unsolicited=%d malformed=%d', unexpected, malformed)
    return reasons


def review_consistency(raw, extraction):
    if not isinstance(raw, dict) or not isinstance(raw.get('findings'), list) or not isinstance(raw.get('summary'), str):
        raise ValueError('invalid_consistency_envelope')
    registry = {r.id: r for r in extraction.requirements}
    findings = []
    for index, item in enumerate(raw['findings'], 1):
        if not isinstance(item, dict):
            raise ValueError('invalid_consistency_schema')
        if 'evidence' in item or 'id' in item:
            # Strictly validate legacy rows instead of repairing them by overwriting evidence.
            row = ConsistencyFinding.model_validate(item)
        else:
            f = QuickConsistencyFinding.model_validate(item)
            ids = f.requirement_ids
            if len(ids) != len(set(ids)) or any(rid not in registry for rid in ids):
                raise ValueError('invalid_consistency_requirement_ids')
            evidence = [FindingEvidence(requirement_id=rid, quote=registry[rid].text,
                        source=registry[rid].source, page=registry[rid].page,
                        paragraph=registry[rid].paragraph) for rid in ids]
            row = ConsistencyFinding(**f.model_dump(), id=f'CON-{index:03d}', evidence=evidence)
        if len(row.requirement_ids) != len(set(row.requirement_ids)):
            raise ValueError('duplicate_consistency_requirement_ids')
        findings.append(row)
    result = ConsistencyAnalysisResult(findings=findings, summary=raw['summary'])
    validate_findings(result, extraction)
    return result


QUICK_CONTRACT = '''
QUICK OUTPUT CONTRACT (overrides the previous request to generate evidence fields):
Return extraction with exact source text and source coordinates. In quality, return
one assessment per extracted requirement_id. Within each assessment, findings refer
ONLY to the parent requirement; do not repeat requirement_id or emit evidence fields.
Set status=passed if and only if findings is empty. Otherwise use needs_revision or
needs_clarification. Do not omit assessments or invent scores for missing analysis.
For consistency findings choose existing requirement_ids; do not emit finding id,
quotes, evidence objects or coordinates. The application generates these from the
verified source registry. Select only relevant IDs, and at least two distinct IDs
for contradiction, duplicate or inconsistent_constraint. Explain the substantive
relationship to the selected requirements; exact source binding is not proof of
semantic correctness. For a targeted correction use the provided verified extraction,
return ONLY the requested quality assessments, and return consistency only if it
is requested. Do not repeat or alter locked extraction or accepted assessments.
'''


def quick_validate(sections):
    instruction = (BASE_DIR / 'prompts' / 'quick_validator.txt').read_text(encoding='utf-8') + QUICK_CONTRACT
    sources = sorted({section['source'] for section in sections}, key=str.casefold)
    source_mapping = {source: f'D{i:02d}' for i, source in enumerate(sources, 1)}
    payload = {'source_mapping': source_mapping, 'sections': sections}
    extraction = None
    retained = {}
    consistency = None
    code = 'invalid_response_schema'
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if attempt > 1 and (notify := request_retry.get()):
            notify()
        schema = QuickAgentResponse if extraction is None else QuickRepairResponse
        response = ask_gemini(prompt=json.dumps(payload, ensure_ascii=False),
                              system_instruction=instruction, response_schema=schema)
        try:
            raw = json.loads(response)
            if not isinstance(raw, dict):
                raise ValueError('invalid_envelope')
        except (ValueError, TypeError, RecursionError):
            raw = None
            code = 'invalid_response_schema'
        quality_reasons = {}
        if raw is not None:
            if extraction is None:
                try:
                    candidate = ExtractionResult.model_validate(raw.get('extraction'))
                    validate_extraction(candidate, sections, source_mapping)
                except ValidationError:
                    code = 'invalid_response_schema'
                except QuickValidationError as error:
                    code = error.code
                else:
                    extraction = candidate
            elif 'extraction' in raw:
                # Compatibility with legacy mocks: never accept changes to locked extraction.
                try:
                    unchanged = ExtractionResult.model_validate(raw['extraction']) == extraction
                except ValidationError:
                    unchanged = False
                if not unchanged:
                    raw = None
                    code = 'invalid_extraction_ids'
            if extraction is not None and raw is not None:
                registry = {r.id: r for r in extraction.requirements}
                quality_reasons = review_quality(raw.get('quality'), registry, retained)
                if consistency is None:
                    try:
                        consistency = review_consistency(raw.get('consistency'), extraction)
                    except (ValueError, TypeError, KeyError):
                        consistency = None
                if len(retained) == len(registry) and consistency is not None:
                    result = QuickValidationResult(extraction=extraction,
                        quality=QualityAnalysisResult(assessments=[retained[r.id] for r in extraction.requirements]),
                        consistency=consistency)
                    validate_quick_result(result, sections, source_mapping)
                    logger.info('Quick output validated attempt=%d', attempt)
                    return result
                code = ('invalid_quality_ids_or_evidence' if len(retained) != len(registry)
                        else 'invalid_consistency_ids_or_evidence')
        logger.warning('Quick validation failed attempt=%d/%d code=%s', attempt, MAX_ATTEMPTS, code)
        if extraction is None:
            payload['correction'] = {'code': code, 'instruction': 'Return a complete schema-valid response with verified extraction.'}
        else:
            missing = [r.id for r in extraction.requirements if r.id not in retained]
            reasons = Counter(quality_reasons.get(rid, 'missing_or_invalid_assessment') for rid in missing)
            logger.warning('Quick repair pending quality=%d accepted=%d consistency=%s reasons=%s',
                           len(missing), len(retained), 'pending' if consistency is None else 'accepted', dict(reasons))
            payload = {'source_mapping': source_mapping, 'sections': sections,
                       'verified_extraction': extraction.model_dump(mode='json'),
                       'correction': {'code': code, 'requested_quality_ids': missing,
                           'quality_diagnostics': [{'requirement_id': rid,
                               'code': quality_reasons.get(rid, 'missing_or_invalid_assessment')} for rid in missing],
                           'request_consistency': consistency is None,
                           'instruction': 'Return only requested assessments. If none, use an empty assessments list. '
                               'Return consistency only when requested; otherwise null. Do not repeat extraction.'}}
    raise QuickValidationError(code)
