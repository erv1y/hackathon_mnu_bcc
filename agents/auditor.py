
import json

from pathlib import Path
from collections import Counter

from core.llm_client import ask_gemini
from core.document_reader import read_document

from core.models import (
    ExtractionResult,
    QualityAnalysisResult,
    ConsistencyAnalysisResult,
    AuditResult
)


BASE_DIR = Path(__file__).resolve().parent.parent

PROMPT_PATH = BASE_DIR / "prompts" / "auditor.txt"

MAX_ATTEMPTS = 3


def prepare_findings(
    quality_result,
    consistency_result
):

    findings = []
    number = 1

    for assessment in quality_result.assessments:

        for finding in assessment.findings:

            item = finding.model_dump()

            item["id"] = f"QUAL-{number:03d}"
            item["agent"] = "quality_analyst"

            item["requirement_ids"] = [
                finding.requirement_id
            ]

            findings.append(item)

            number += 1

    for finding in consistency_result.findings:

        item = finding.model_dump()

        item["agent"] = "consistency_analyst"

        findings.append(item)

    return findings


def clean_response(response):

    response = response.strip()

    if response.startswith("```"):
        response = response.removeprefix("```json")
        response = response.removeprefix("```")
        response = response.removesuffix("```")

    return response.strip()


def normalize_response(data):

    if not isinstance(data, dict):
        raise ValueError(
            "Audit response must be a JSON object."
        )

    decisions = data.get("decisions")

    if not isinstance(decisions, list):
        raise ValueError(
            "Decisions must be a JSON array."
        )

    for decision in decisions:

        if not isinstance(decision, dict):
            raise ValueError(
                "Invalid decision object."
            )

        finding_id = decision.get(
            "finding_id",
            "unknown"
        )

        status = decision.get("decision")

        if status in ("merged", "rejected"):

            value = decision.get("final_severity")

            if (
                isinstance(value, str)
                and value.strip().lower()
                in ("null", "none")
            ):

                decision["final_severity"] = None

                print(
                    f"[NORMALIZER] {finding_id}: "
                    "final_severity converted to null"
                )

        if status in (
            "confirmed",
            "revised",
            "rejected"
        ):

            value = decision.get("merged_into")

            if (
                isinstance(value, str)
                and value.strip().lower()
                in ("null", "none", "")
            ):

                decision["merged_into"] = None

                print(
                    f"[NORMALIZER] {finding_id}: "
                    "merged_into converted to null"
                )

    return data


RETAINED = {"confirmed", "revised"}
SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}
READINESS_RANK = {"ready": 0, "conditionally_ready": 1, "not_ready": 2}


def norm(value):
    return " ".join(value.split())


def meaningful(value):
    return bool(norm(value)) and norm(value).casefold() not in {"null", "none", "n/a", "-", "—"}


def overlap_hints(findings):
    """Small deterministic shortlist for review, never an automatic merge."""
    from itertools import combinations
    hints = []
    for a, b in combinations(findings, 2):
        shared = sorted(set(a["requirement_ids"]) & set(b["requirement_ids"]))
        if shared:
            hints.append({"finding_ids": [a["id"], b["id"]], "shared_requirement_ids": shared})
    return hints[:120]


def validate_source_registry(sections, extraction):
    reqs = {r.id: r for r in extraction.requirements}
    if not reqs or len(reqs) != len(extraction.requirements):
        raise ValueError("Empty extraction or duplicate requirement IDs")
    for r in reqs.values():
        if not norm(r.text) or not any(
            x["source"] == r.source and x.get("page") == r.page
            and x.get("paragraph") == r.paragraph and norm(r.text) in norm(x["text"])
            for x in sections
        ):
            raise ValueError(f"Source registry mismatch: {r.id}")
    return reqs


def validate_audit(result, findings, extraction_result):
    """Check IDs, final evidence, and conservative merge invariants, not semantics."""
    original = {f["id"]: f for f in findings}
    reqs = {r.id: r for r in extraction_result.requirements}
    decisions = {d.finding_id: d for d in result.decisions}
    if len(original) != len(findings) or len(reqs) != len(extraction_result.requirements):
        raise ValueError("Duplicate input IDs")
    if Counter(f["id"] for f in findings) != Counter(d.finding_id for d in result.decisions):
        raise ValueError("Auditor must review every original finding exactly once")
    for d in result.decisions:
        fid = d.finding_id
        ids = set(d.requirement_ids)
        orig_ids = set(original[fid]["requirement_ids"])
        if not ids or len(ids) != len(d.requirement_ids) or not ids <= reqs.keys():
            raise ValueError(f"{fid}: invalid or repeated requirement IDs")
        if not all(meaningful(x) for x in (d.title, d.explanation, d.recommendation, d.audit_reason)):
            raise ValueError(f"{fid}: empty or placeholder decision text")
        if d.decision in RETAINED:
            if d.final_severity is None or d.merged_into is not None:
                raise ValueError(f"{fid}: invalid retained decision")
        elif d.final_severity is not None:
            raise ValueError(f"{fid}: removed decision requires null severity")
        if d.decision == "rejected":
            if d.merged_into is not None or d.final_evidence or ids != orig_ids:
                raise ValueError(f"{fid}: rejected decision must keep original IDs and no final evidence")
            continue
        if not meaningful(d.root_cause) or not meaningful(d.scope):
            raise ValueError(f"{fid}: missing root cause or scope")
        evidence_ids = set()
        for e in d.final_evidence:
            if e.requirement_id not in reqs:
                raise ValueError(f"{fid}: unknown evidence ID")
            r = reqs[e.requirement_id]
            if not meaningful(e.quote) or norm(e.quote) not in norm(r.text):
                raise ValueError(f"{fid}: invalid final quotation for {r.id}")
            if (e.source, e.page, e.paragraph) != (r.source, r.page, r.paragraph):
                raise ValueError(f"{fid}: incorrect final source for {r.id}")
            evidence_ids.add(r.id)
        if evidence_ids != ids:
            raise ValueError(f"{fid}: final evidence must cover exactly the decision IDs")
        if d.decision == "merged":
            target = decisions.get(d.merged_into)
            if target is None or target.finding_id == fid or target.decision not in RETAINED:
                raise ValueError(f"{fid}: merge must point directly to a retained finding")
            target_ids = set(target.requirement_ids)
            if (norm(d.root_cause).casefold(), norm(d.scope).casefold()) != (norm(target.root_cause).casefold(), norm(target.scope).casefold()):
                raise ValueError(f"{fid}: merge root cause/scope mismatch")
            if not ids & orig_ids & target_ids:
                raise ValueError(f"{fid}: merge lacks a shared source anchor")
        else:
            group_ids = orig_ids | {rid for m in result.decisions if m.decision == "merged" and m.merged_into == fid for rid in original[m.finding_id]["requirement_ids"]}
            if not ids & group_ids:
                raise ValueError(f"{fid}: retained finding unrelated to original group")
            if d.decision == "confirmed" and (d.final_severity != original[fid]["severity"] or ids != orig_ids):
                d.decision = "revised"
    return result


def apply_readiness_floor(result, logic_debug=None):
    """Conservative policy floor; no claim of complete verification."""
    retained = [d for d in result.decisions if d.decision in RETAINED]
    floor = "ready"
    if retained or result.extraction_audit.status != "complete" or result.extraction_audit.missing_requirements or result.extraction_audit.unsupported_requirements:
        floor = "conditionally_ready"
    if any(d.final_severity in ("high", "critical") for d in retained):
        floor = "not_ready"
    if logic_debug is not None and not logic_debug.get("complete", False):
        if floor == "ready": floor = "conditionally_ready"
        warning = "Парный анализ завершён не полностью; отсутствие замечаний не означает отсутствие дефектов."
        if warning not in result.limitations: result.limitations.append(warning)
    if READINESS_RANK[result.readiness] < READINESS_RANK[floor]:
        result.readiness = floor
    counts = Counter(d.final_severity for d in retained)
    statuses = Counter(d.decision for d in result.decisions)
    result.executive_summary = (
        f"Статус: {result.readiness}. Сохранено замечаний: {len(retained)} "
        f"(critical: {counts['critical']}, high: {counts['high']}, medium: {counts['medium']}, low: {counts['low']}). "
        f"Объединено: {statuses['merged']}; отклонено: {statuses['rejected']}. "
        f"Аудит извлечения: {result.extraction_audit.status}. "
        "Итог отражает проверенные кандидаты и не гарантирует отсутствие других дефектов."
    )
    return result


def audit_trace(result, findings):
    """Original findings are immutable; expose the final groups and audit decisions."""
    retained = [d for d in result.decisions if d.decision in RETAINED]
    return {"version": "2.0", "input_count": len(findings), "retained_count": len(retained),
            "merged_count": sum(d.decision == "merged" for d in result.decisions),
            "rejected_count": sum(d.decision == "rejected" for d in result.decisions),
            "groups": [{"representative": d.finding_id, "members": [d.finding_id] + [m.finding_id for m in result.decisions if m.decision == "merged" and m.merged_into == d.finding_id], "root_cause": d.root_cause, "scope": d.scope, "final_evidence": [e.model_dump(mode="json") for e in d.final_evidence]} for d in retained],
            "original_findings": findings, "audit": result.model_dump(mode="json")}


def audit_results(
    sections,
    extraction_result,
    quality_result,
    consistency_result,
    logic_debug=None
):

    reqs = validate_source_registry(sections, extraction_result)
    if Counter(a.requirement_id for a in quality_result.assessments) != Counter(reqs.keys()):
        raise ValueError("Quality assessment registry mismatch")
    from agents.consistency_analyst import validate_findings
    validate_findings(consistency_result, extraction_result)
    for a in quality_result.assessments:
        for f in a.findings:
            if f.requirement_id != a.requirement_id or not norm(f.evidence) or norm(f.evidence) not in norm(reqs[a.requirement_id].text):
                raise ValueError(f"Invalid quality source evidence: {a.requirement_id}")
    print("\n[AGENT 4] Auditor 2.0 started")

    findings = prepare_findings(
        quality_result,
        consistency_result
    )

    print(
        f"[AGENT 4] Reviewing "
        f"{len(findings)} findings"
    )

    system_prompt = PROMPT_PATH.read_text(
        encoding="utf-8"
    )

    system_prompt += """

STRICT JSON RULES:

Use JSON null, not the string "null".

For confirmed and revised findings:
- final_severity must be critical, high, medium or low.
- merged_into must be null.

For rejected findings:
- final_severity must be null.
- merged_into must be null.

For merged findings:
- final_severity must be null.
- merged_into must reference an existing retained finding.

If severity changes, use revised instead of confirmed.

Never invent requirement IDs or finding IDs.

Return exactly one decision per input finding.

Return valid JSON only.

"""

    audit_input = {
        "original_document": sections,
        "extracted_requirements":
            extraction_result.model_dump(),
        "findings": findings,
        "overlap_hints": overlap_hints(findings),
        "counterexample_coverage": {k: logic_debug[k] for k in ("complete", "total_possible_pairs", "reviewed_pair_ids", "unresolved_pair_ids") if k in logic_debug} if logic_debug is not None else None
    }

    current_prompt = json.dumps(
        audit_input,
        ensure_ascii=False
    )

    last_error = None

    for attempt in range(1, MAX_ATTEMPTS + 1):

        print(
            f"[AGENT 4] Validation attempt "
            f"{attempt}/{MAX_ATTEMPTS}"
        )

        response = ask_gemini(
            prompt=current_prompt,
            system_instruction=system_prompt,
            model="gemini-3.5-flash-lite",
            response_schema=AuditResult
        )

        response = clean_response(response)

        try:

            data = json.loads(response)

            data = normalize_response(data)

            result = AuditResult.model_validate(data)

            result = validate_audit(
                result,
                findings,
                extraction_result
            )

            result = apply_readiness_floor(result, logic_debug)
            print(
                "[VALIDATOR] Audit validation passed"
            )

            counts = Counter(
                d.decision
                for d in result.decisions
            )

            for status, count in counts.items():

                print(
                    f"[AUDIT] {status}: {count}"
                )

            retained = [
                d for d in result.decisions
                if d.decision in (
                    "confirmed",
                    "revised"
                )
            ]

            print(
                f"[AGENT 4] Final retained findings: "
                f"{len(retained)}"
            )

            return result

        except ValueError as error:

            last_error = error

            print(
                f"[VALIDATION FAILED] {error}"
            )

            if attempt == MAX_ATTEMPTS:
                break

            print(
                "[SELF-CORRECTION] "
                "Requesting corrected audit..."
            )

            current_prompt = json.dumps(
                {
                    "task": (
                        "Correct the previous audit "
                        "response and return complete "
                        "valid JSON."
                    ),

                    "original_input": audit_input,

                    "previous_response": response,

                    "validation_error": str(error),

                    "instructions": [
                        "Fix all validation errors.",
                        "Preserve valid decisions.",
                        "Use actual JSON null values.",
                        "Do not invent findings.",
                        "Review every original finding.",
                        "Return complete valid JSON."
                    ]
                },
                ensure_ascii=False
            )

    raise RuntimeError(
        "Auditor failed validation after "
        "all correction attempts."
    ) from last_error


def main():
    import argparse
    p = argparse.ArgumentParser(description="Audit saved findings without rerunning extraction or analysts")
    p.add_argument("--extraction", type=Path, required=True)
    p.add_argument("--quality", type=Path, required=True)
    p.add_argument("--consistency", type=Path, required=True)
    p.add_argument("--document", type=Path, nargs="+", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--debug", type=Path)
    p.add_argument("--logic-debug", type=Path)
    a = p.parse_args()
    extraction = ExtractionResult.model_validate_json(a.extraction.read_text(encoding="utf-8"))
    quality = QualityAnalysisResult.model_validate_json(a.quality.read_text(encoding="utf-8"))
    consistency = ConsistencyAnalysisResult.model_validate_json(a.consistency.read_text(encoding="utf-8"))
    sections = [s for doc in a.document for s in read_document(doc)]
    from agents.consistency_analyst import validate_findings
    validate_findings(consistency, extraction)
    if Counter(x.requirement_id for x in quality.assessments) != Counter(r.id for r in extraction.requirements):
        raise ValueError("Quality registry mismatch")
    logic = json.loads(a.logic_debug.read_text(encoding="utf-8")) if a.logic_debug else None
    result = audit_results(sections, extraction, quality, consistency, logic)
    debug_path = a.debug or a.output.with_name("audit_trace_v08.json")
    if debug_path.resolve() == a.output.resolve():
        raise ValueError("Output and debug paths must differ")
    for dest, body in ((a.output, result.model_dump_json(indent=2)), (debug_path, json.dumps(audit_trace(result, prepare_findings(quality, consistency)), ensure_ascii=False, indent=2))):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body, encoding="utf-8")
        print(f"[SAVED] {dest}")
    for d in result.decisions:
        print(f"{d.finding_id} {d.decision} {d.final_severity or '-'} {d.merged_into or ''} | {d.title}")
    print(f"[READINESS] {result.readiness}")


if __name__ == "__main__":
    main()
