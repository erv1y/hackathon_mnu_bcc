"""Shared counters for completed, independently audited runs."""
from collections import Counter
from statistics import mean


def needs_human_review(row):
    return row.finding_id.startswith("REVIEW-") or row.assessment_kind != "defect"


def audit_metrics(result):
    retained = [row for row in result.audit.decisions if row.decision in ("confirmed", "revised")]
    confirmed = [row for row in retained if not needs_human_review(row)]
    distribution = Counter(row.final_severity for row in confirmed)
    scores = [row.quality_score for row in result.quality.assessments]
    return {
        "unique_findings": len(confirmed),
        "human_review": len(retained) - len(confirmed),
        "merged_duplicates": sum(row.decision == "merged" for row in result.audit.decisions),
        "counts": {s.title(): distribution[s] for s in ("critical", "high", "medium", "low")},
        "score": round(mean(scores), 1) if scores and not getattr(result, "passport", {}).get("source_warnings") else None,
        "score_formula": "mean(requirement_quality_scores)",
    }
