
import json

from pathlib import Path
from collections import Counter
from pydantic import BaseModel

from core.llm_client import ask_gemini

from core.models import (
    ExtractionResult,
    QualityAnalysisResult,
    ConsistencyAnalysisResult
)

from agents.consistency_analyst import (
    validate_findings
)


BASE_DIR = Path(__file__).resolve().parent.parent


class QuickValidationResult(BaseModel):

    extraction: ExtractionResult

    quality: QualityAnalysisResult

    consistency: ConsistencyAnalysisResult


def quick_validate(sections):

    print("\n[QUICK AGENT] Started")

    instruction = (
        BASE_DIR
        / "prompts"
        / "quick_validator.txt"
    ).read_text(encoding="utf-8")

    sources = sorted(
        {s["source"] for s in sections},
        key=str.casefold
    )

    source_mapping = {

        source: f"D{i:02d}"

        for i, source in enumerate(
            sources, 1
        )
    }

    prompt = json.dumps(
        {
            "source_mapping": source_mapping,
            "sections": sections
        },
        ensure_ascii=False
    )

    response = ask_gemini(

        prompt=prompt,

        system_instruction=instruction,

        response_schema=QuickValidationResult
    )

    result = (
        QuickValidationResult
        .model_validate_json(response)
    )

    requirements = result.extraction.requirements

    ids = [r.id for r in requirements]

    if not ids or len(ids) != len(set(ids)):

        raise ValueError(
            "Empty extraction or duplicate IDs"
        )

    for requirement in requirements:

        quote = " ".join(
            requirement.text.split()
        )

        supported = any(

            section["source"] == requirement.source

            and section.get("page")
            == requirement.page

            and section.get("paragraph")
            == requirement.paragraph

            and quote in " ".join(
                section["text"].split()
            )

            for section in sections
        )

        if not quote or not supported:

            raise ValueError(
                f"Unsupported requirement: "
                f"{requirement.id}"
            )

        if len(sources) > 1:

            prefix = (
                source_mapping[requirement.source]
                + "::"
            )

            if not requirement.id.startswith(prefix):

                raise ValueError(
                    f"Invalid document ID: "
                    f"{requirement.id}"
                )

    expected = Counter(ids)

    actual = Counter(

        a.requirement_id

        for a in result.quality.assessments
    )

    if expected != actual:

        raise ValueError(
            "Invalid quality assessment IDs"
        )

    for assessment in result.quality.assessments:

        for finding in assessment.findings:

            if (
                finding.requirement_id
                != assessment.requirement_id
            ):

                raise ValueError(
                    "Invalid quality finding ID"
                )

    validate_findings(
        result.consistency,
        result.extraction
    )

    print("[VALIDATOR] Quick output validated")

    print(
        f"[QUICK AGENT] Requirements: "
        f"{len(requirements)}"
    )

    print(
        f"[QUICK AGENT] Quality issues: "
        f"{sum(len(a.findings) for a in result.quality.assessments)}"
    )

    print(
        f"[QUICK AGENT] Consistency issues: "
        f"{len(result.consistency.findings)}"
    )

    return result
