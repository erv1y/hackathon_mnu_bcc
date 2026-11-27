"""Single-agent preliminary validation with a Gemini-facing response schema."""

import json
import logging
from pathlib import Path

from pydantic import BaseModel, ValidationError

from agents.consistency_analyst import validate_findings
from agents.quality_analyst import validate_quality
from core.llm_client import ask_gemini, request_retry
from core.models import (
    ConsistencyAnalysisResult,
    ConsistencyFinding,
    ExtractionResult,
    QualityAnalysisResult,
)

BASE_DIR = Path(__file__).resolve().parent.parent
MAX_ATTEMPTS = 2
logger = logging.getLogger(__name__)


class QuickConsistencyResponse(BaseModel):
    findings: list[ConsistencyFinding]
    summary: str


class QuickAgentResponse(BaseModel):
    extraction: ExtractionResult
    quality: QualityAnalysisResult
    consistency: QuickConsistencyResponse


class QuickValidationResult(BaseModel):
    extraction: ExtractionResult
    quality: QualityAnalysisResult
    consistency: ConsistencyAnalysisResult


class QuickValidationError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def validate_quick_result(result, sections, source_mapping):
    requirements = result.extraction.requirements
    ids = {requirement.id for requirement in requirements}
    if not ids or len(ids) != len(requirements) or any(not rid.strip() for rid in ids):
        raise QuickValidationError("invalid_extraction_ids")

    for requirement in requirements:
        quote = " ".join(requirement.text.split())
        supported = quote and any(
            (section["source"], section.get("page"), section.get("paragraph"))
            == (requirement.source, requirement.page, requirement.paragraph)
            and quote in " ".join(section["text"].split())
            for section in sections
        )
        if not supported:
            raise QuickValidationError("unsupported_extraction_evidence")
        if len(source_mapping) > 1 and not requirement.id.startswith(
            source_mapping[requirement.source] + "::"
        ):
            raise QuickValidationError("invalid_document_id")

    try:
        validate_quality(result.quality, result.extraction)
    except ValueError:
        raise QuickValidationError("invalid_quality_ids_or_evidence") from None
    try:
        validate_findings(result.consistency, result.extraction)
    except ValueError:
        raise QuickValidationError("invalid_consistency_ids_or_evidence") from None


def quick_validate(sections):
    instruction = (BASE_DIR / "prompts" / "quick_validator.txt").read_text(encoding="utf-8")
    sources = sorted({section["source"] for section in sections}, key=str.casefold)
    source_mapping = {source: f"D{i:02d}" for i, source in enumerate(sources, 1)}
    payload = {"source_mapping": source_mapping, "sections": sections}

    for attempt in range(1, MAX_ATTEMPTS + 1):
        if attempt > 1 and (notify := request_retry.get()):
            notify()
        response = ask_gemini(
            prompt=json.dumps(payload, ensure_ascii=False),
            system_instruction=instruction,
            response_schema=QuickAgentResponse,
        )
        try:
            parsed = QuickAgentResponse.model_validate_json(response)
            result = QuickValidationResult(
                extraction=parsed.extraction,
                quality=parsed.quality,
                consistency=ConsistencyAnalysisResult(
                    findings=parsed.consistency.findings,
                    summary=parsed.consistency.summary,
                ),
            )
            validate_quick_result(result, sections, source_mapping)
        except ValidationError:
            code = "invalid_response_schema"
        except QuickValidationError as error:
            code = error.code
        else:
            logger.info("Quick output validated attempt=%d", attempt)
            return result

        logger.warning("Quick validation failed attempt=%d/%d code=%s", attempt, MAX_ATTEMPTS, code)
        payload["correction"] = {
            "code": code,
            "instruction": "Return a complete schema-valid response. Verify every ID, quotation and source coordinate.",
        }

    raise QuickValidationError(code)
