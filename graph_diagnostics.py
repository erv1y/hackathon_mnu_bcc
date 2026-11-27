"""Offline diagnostic scoring over the exact inputs and outputs of a graph run."""
from collections import Counter
import re

from evaluation import _match_findings, _matches, _ratio, _search_text, measure_case, summarize

LEVELS = ("Low", "Medium", "High", "Critical")


def wording(text):
    return " ".join(re.findall(r"\w+", text.casefold()))


def diagnostic_case(case, report, decisions=None):
    decisions = decisions or {}
    report = {key: value for key, value in report.items() if key != "auditor_retry"}
    predictions = [row for module in report["modules"] for row in module["defects"]]
    active = [row for row in predictions if row.get("review_reason") is None]
    legacy_active = [dict(row, criterion_id=row.get("original_criterion_id", row["criterion_id"]))
                     for row in active]
    strict_report = {**report, "modules": [{"defects": legacy_active}]}
    strict = measure_case(case, strict_report)
    passport = report["native"]["passport"]
    requirements = report["native"]["extraction"]["requirements"]
    extracted = [" ".join(row["text"].split()) for row in requirements]
    strict.update(
        false_positives=strict["predicted"] - strict["matched"],
        false_negatives=strict["expected"] - strict["matched"],
        extraction_matched=sum(any(" ".join(ref.split()) in text for text in extracted)
                               for ref in case.reference_requirements),
        extraction_expected=len(case.reference_requirements),
        api_attempts=passport["api_attempts"], tokens=passport["reported_tokens"],
    )
    sources = {"first.md": case.text, "second.md": case.companion}
    authentic = [[bool(q["text"].strip()) and
                  " ".join(q["text"].split()) in " ".join(sources.get(q.get("file_name"), "").split())
                  for q in row["evidence"]] for row in predictions]
    by_id = {id(row): j for j, row in enumerate(predictions)}
    strict_matches = {i: by_id[id(active[j])] for i, j in _match_findings(case.expected, legacy_active).items()}
    pairs = []
    normalized_keys = {f"{i}:{j}" for i in range(len(case.expected)) for j in range(len(predictions))}
    if set(decisions) - normalized_keys:
        raise ValueError(f"{case.id}: unknown expert pair")
    for i, label in enumerate(case.expected):
        for j, row in enumerate(predictions):
            key = f"{i}:{j}"
            review = decisions.get(key, {})
            if set(review) - {"same_defect", "relevant_evidence", "recommendation_adequate"}:
                raise ValueError(f"{case.id}: invalid expert decision fields")
            if any(type(value) is not bool for value in review.values()):
                raise ValueError(f"{case.id}: expert judgments must be boolean")
            evidence = row["evidence"]
            anchors = bool(label.anchors) and all(
                any(ok and _search_text(anchor) in _search_text(q["text"])
                    for q, ok in zip(evidence, authentic[j])) for anchor in label.anchors)
            description = getattr(label, "description", "")
            same_wording = bool(wording(description)) and wording(description) == wording(row.get("issue", ""))
            semantic = review.get("same_defect", anchors and same_wording)
            pairs.append({
                "expected_index": i, "prediction_index": j,
                "strict_eligible": row in active and _matches(label, dict(row, criterion_id=row.get("original_criterion_id", row["criterion_id"]))),
                "strict_reasons": [reason for reason, failed in (
                    ("manual_review_candidate", row not in active),
                    ("criterion_mismatch", label.criterion_id != row.get("original_criterion_id", row["criterion_id"])),
                    ("missing_criterion_metadata", not row.get("criterion")),
                    ("evidence_status_mismatch", label.evidence != row["verified"]),
                    ("expected_anchor_missing", bool(label.anchors) and not all(
                        any(_search_text(anchor) in _search_text(q["text"]) for q in evidence)
                        for anchor in label.anchors)),
                ) if failed],
                "semantic_candidate": semantic and row in active,
                "semantic_reason": ("manual_review_candidate" if row not in active else
                                    "expert_same_defect" if review.get("same_defect") is True else
                                    "expert_distinct_defect" if review.get("same_defect") is False else
                                    "same_description_with_authentic_anchors" if semantic else
                                    "expert_review_required_shared_anchors" if anchors else
                                    "expert_review_required_missing_authentic_anchors"),
                "evidence_relevant": review.get("relevant_evidence", True if semantic and anchors else None),
                "recommendation_adequate": review.get("recommendation_adequate"),
            })

    owner = {}
    options = [[] for _ in case.expected]
    for pair in pairs:
        if pair["semantic_candidate"]:
            options[pair["expected_index"]].append(pair["prediction_index"])

    def assign(i, seen):
        for j in options[i]:
            if j in seen:
                continue
            seen.add(j)
            if j not in owner or assign(owner[j], seen):
                owner[j] = i
                return

    for i in sorted(range(len(options)), key=lambda i: (len(options[i]), i)):
        assign(i, set())
    matches = {i: j for j, i in owner.items()}
    used = set(owner)
    duplicates = {j for options_for_label in options for j in options_for_label if j not in used}
    signatures = set()
    for j, row in enumerate(predictions):
        signature = (wording(row.get("issue", "")), tuple(sorted(
            (q.get("file_name", ""), _search_text(q["text"])) for q in row["evidence"])))
        if row in active and j not in used and all(signature) and signature in signatures:
            duplicates.add(j)
        if row in active:
            signatures.add(signature)

    selected_strict = set(strict_matches.values())
    prediction_outcomes = []
    for j, row in enumerate(predictions):
        if row.get("review_reason") is not None:
            status = "manual_review_candidate"
        elif j in used:
            status = "matched"
        elif j in duplicates:
            status = "duplicate"
        elif not case.expected:
            status = "unexpected_in_clean_case_expert_review"
        elif all(decisions.get(f"{i}:{j}", {}).get("same_defect") is False
                 for i in range(len(case.expected))):
            status = "expert_confirmed_no_match"
        else:
            status = "unmatched_expert_review"
        prediction_outcomes.append({"prediction_index": j, "strict_matched": j in selected_strict,
                                    "semantic_status": status})
    for pair in pairs:
        i, j = pair["expected_index"], pair["prediction_index"]
        pair["strict_selected"] = strict_matches.get(i) == j
        pair["semantic_selected"] = matches.get(i) == j
        if pair["semantic_candidate"] and not pair["semantic_selected"]:
            pair["semantic_reason"] = "not_selected_one_to_one"
    row_metrics = Counter(expected=len(case.expected), predictions=len(active),
                          matched=len(matches), duplicates=len(duplicates),
                          manual=len(predictions) - len(active), findings=len(predictions),
                          quotes=sum(map(len, authentic)), authentic=sum(map(sum, authentic)),
                          evidence_missing=sum(not row["evidence"] for row in predictions),
                          expert_review_pairs=sum(p["semantic_reason"].startswith("expert_review") for p in pairs))
    confusion = Counter()
    for i, label in enumerate(case.expected):
        target = label.severity
        row_metrics[f"expected_{target}"] += 1
        if i not in matches:
            confusion[target, "Undetected"] += 1
            continue
        j = matches[i]
        row = predictions[j]
        actual = row["severity"]
        confusion[target, actual] += 1
        row_metrics[f"detected_{target}"] += 1
        row_metrics["criterion_exact"] += row["criterion_id"] == label.criterion_id
        row_metrics["criterion_acceptable"] += row["criterion_id"] in {
            label.criterion_id, *getattr(label, "acceptable_classifications", [])}
        row_metrics["severity_exact"] += actual == target
        row_metrics["severity_under"] += LEVELS.index(actual) < LEVELS.index(target)
        row_metrics["severity_over"] += LEVELS.index(actual) > LEVELS.index(target)
        pair = pairs[i * len(predictions) + j]
        for field in ("evidence_relevant", "recommendation_adequate"):
            value = pair[field]
            row_metrics[f"{field}_assessed"] += value is not None
            row_metrics[field] += value is True
    status_counts = {}
    for status in ("audited", "preliminary"):
        status_counts[status] = {
            "expected": len(case.expected),
            "predictions": sum(row.get("audit_status") == status for row in active),
            "matched": sum(predictions[j].get("audit_status") == status for j in matches.values()),
        }
    return {
        "finding_status_counts": status_counts,
        "scenario": case.model_dump(mode="json"), "report": report,
        "expert_decisions": decisions, "strict": strict, "matching": pairs,
        "predictions": predictions, "prediction_outcomes": prediction_outcomes,
        "quote_authenticity": authentic,
        "duplicate_prediction_indices": sorted(duplicates),
        "diagnostic_counts": dict(row_metrics),
        "independently_audited": report.get("independent_audit", False) and not report.get("partial", False),
        "partially_audited": report.get("finding_audit_completed", False) and report.get("partial", False),
        "end_to_end_success": not report.get("partial") and not report["failed_agents"]
                               and report["coverage"]["agents_completed"] == report["coverage"]["agents_total"],
        "severity_confusion": {level: {actual: confusion[level, actual]
                                      for actual in (*LEVELS, "Undetected")} for level in LEVELS},
    }


def aggregate(records):
    strict = [record["strict"] for record in records]
    totals = {key: sum(row[key] for row in strict) for key in
              ("false_positives", "false_negatives", "extraction_matched", "extraction_expected", "api_attempts")}
    legacy = {**summarize(strict), **totals,
              "extraction_coverage": (totals["extraction_matched"] / totals["extraction_expected"]
                                      if totals["extraction_expected"] else None),
              "execution_seconds": round(sum(row["elapsed_sec"] for row in strict), 2)}
    counts = Counter()
    for record in records:
        counts.update(record["diagnostic_counts"])
    m = counts
    diagnostic = {
        "semantic_confirmed_precision": _ratio(m["matched"], m["predictions"]),
        "semantic_confirmed_recall": _ratio(m["matched"], m["expected"]),
        "semantic_confirmed_f1": _ratio(2 * m["matched"], m["expected"] + m["predictions"]),
        "criterion_accuracy": _ratio(m["criterion_exact"], m["matched"]),
        "acceptable_criterion_accuracy": _ratio(m["criterion_acceptable"], m["matched"]),
        "exact_severity_accuracy": _ratio(m["severity_exact"], m["matched"]),
        "underestimation_rate": _ratio(m["severity_under"], m["matched"]),
        "overestimation_rate": _ratio(m["severity_over"], m["matched"]),
        "high_critical_detection_recall": _ratio(m["detected_High"] + m["detected_Critical"],
                                                 m["expected_High"] + m["expected_Critical"]),
        "critical_detection_recall": _ratio(m["detected_Critical"], m["expected_Critical"]),
        "high_detection_recall": _ratio(m["detected_High"], m["expected_High"]),
        "detection_recall": _ratio(m["matched"], m["expected"]),
        "full_suite_semantic_recall": _ratio(m["matched"], m["expected"]),
        "evidence_authenticity": _ratio(m["authentic"], m["quotes"]),
        "evidence_relevance": _ratio(m["evidence_relevant"], m["evidence_relevant_assessed"]),
        "recommendation_quality": _ratio(m["recommendation_adequate"], m["recommendation_adequate_assessed"]),
        "duplicate_finding_rate": _ratio(m["duplicates"], m["predictions"]),
        "manual_review_candidate_rate": _ratio(m["manual"], m["findings"]),
        "extraction_coverage": legacy["extraction_coverage"],
        "agent_reliability": legacy["agent_success_rate"],
        "full_suite_agent_reliability_lower_bound": legacy["agent_success_rate"],
        "counts": dict(m),
    }
    matrix = {level: {actual: sum(r["severity_confusion"][level][actual] for r in records)
                      for actual in (*LEVELS, "Undetected")} for level in LEVELS}
    diagnostic["severity_confusion"] = matrix
    audited = Counter()
    preliminary = Counter()
    individually_audited = Counter()
    model_usage = Counter()
    fallback_usage = Counter()
    stage_attempts = Counter()
    stage_seconds = Counter()
    stage_samples = Counter()
    for record in records:
        destination = (
            audited if record["independently_audited"]
            else individually_audited if record["partially_audited"]
            else preliminary
        )
        breakdown = record.get("finding_status_counts", {})
        if not record["independently_audited"] and all(
            item.get("audit_status") in {"audited", "preliminary"}
            for item in record["predictions"]
        ) and breakdown:
            if breakdown["audited"]["predictions"] or record["partially_audited"]:
                individually_audited.update(breakdown["audited"])
            if breakdown["preliminary"]["predictions"] or (not breakdown["audited"]["predictions"] and not record["partially_audited"]):
                preliminary.update(breakdown["preliminary"])
        else:
            destination.update(record["diagnostic_counts"])
        passport = record["report"]["native"]["passport"]
        for call in passport.get("model_attempts", []):
            model_usage[call["model"]] += 1
            fallback_usage[call["model"]] += bool(call.get("fallback"))
            stage_attempts[call.get("stage") or "unknown"] += 1
        for stage, elapsed in passport.get("stage_seconds", {}).items():
            stage_seconds[stage] += elapsed
            stage_samples[stage] += 1
    def detection(partition, expected):
        return {
            "matched": partition["matched"], "predictions": partition["predictions"], "expected": expected,
            "precision": _ratio(partition["matched"], partition["predictions"]),
            "recall": _ratio(partition["matched"], expected),
            "f1": _ratio(2 * partition["matched"], partition["predictions"] + expected),
        }

    deep = [r for r in records if r["report"]["native"]["passport"].get("mode") == "deep"
            or r["report"].get("mode") == "deep" or r["independently_audited"]]
    diagnostic.update(
        partial_finding_audit_detection=detection(individually_audited, individually_audited["expected"]),
        full_document_coverage_rate=_ratio(sum(r["independently_audited"] for r in deep), len(deep)),
        preliminary_semantic_detection=detection(preliminary, preliminary["expected"]),
        independently_audited_semantic_detection=detection(audited, m["expected"]),
        end_to_end_success_rate=_ratio(sum(r["end_to_end_success"] for r in records), len(records)),
        incomplete_audit_rate=_ratio(sum(not r["independently_audited"] for r in deep), len(deep)),
        provider_model_attempts=dict(model_usage), fallback_model_attempts=dict(+fallback_usage),
        stage_api_attempts=dict(stage_attempts),
        stage_seconds={stage: round(total, 3) for stage, total in stage_seconds.items()},
        stage_latency_samples=dict(stage_samples),
    )
    for level in ("High", "Critical"):
        diagnostic[f"{level.lower()}_exact_recall"] = _ratio(matrix[level][level], m[f"expected_{level}"])
    return legacy, diagnostic
