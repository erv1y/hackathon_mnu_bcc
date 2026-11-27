"""Project the validated graph result onto the web UI without re-auditing it."""
import json
import re
from collections import Counter
from statistics import mean

from agents.auditor import prepare_findings
from analyzer import build_result
from core.knowledge_graph import build_knowledge_graph, graph_json
from document_review import _fix_plan
from orchestrator import DeepPartialRunResult, DeepRunResult
from schemas import SEVERITIES
from review_insights import build_insights

AGENTS = {
    "extractor": ("payments", "Извлечение требований", "Талаптарды шығару", "Extractor"),
    "quality": ("kyc_aml", "Анализ качества", "Сапаны талдау", "Quality Analyst"),
    "consistency": ("security", "Анализ согласованности", "Сәйкестікті талдау", "Consistency Analyst"),
    "auditor": ("logic", "Аудитор", "Аудитор", "Auditor"),
    "quick": ("quick", "Быстрая проверка", "Жылдам тексеру", "Quick Agent"),
}
MODE_AGENTS = {"deep": ("extractor", "quality", "consistency", "auditor"), "quick": ("quick",)}
CRITERIA = (
    "correctness", "clarity", "completeness", "verifiability", "consistency", "feasibility",
    "necessity", "traceability", "unambiguity", "contradiction", "duplicate", "missing_requirement",
    "inconsistent_constraint", "missing_dependency", "scope_conflict", "terminology_inconsistency",
)


def agent_list(mode, language):
    index = ("ru", "kk", "en").index(language) + 1
    return [{"key": AGENTS[node][0], "title": AGENTS[node][index], "scope": AGENTS[node][index]} for node in MODE_AGENTS[mode]]


def adapt_report(result, bundle, settings, language, timings=None, anchors=None):
    audited = isinstance(result, DeepRunResult)
    partial = isinstance(result, DeepPartialRunResult)
    deep = audited or partial
    mode = "deep" if deep else "quick"
    report = build_result({}, settings, bundle.text, result.execution_time_seconds, mode="rules",
                          language=language, bundle=bundle, check_mode=mode)
    positions = {file["name"]: i for i, file in enumerate(bundle.manifest)}
    requirements = {row.id: row for row in result.extraction.requirements}
    assessments = {row.requirement_id: row for row in result.quality.assessments}
    mappings = {row["report_id"]: row for row in result.passport["id_mapping"]}
    prepared = prepare_findings(result.quality, result.consistency)
    originals = {row["id"]: row for row in prepared}
    decisions = {row.finding_id: row for row in result.audit.decisions} if audited else {}
    timings = timings or {}
    anchors = anchors or {}

    def point(source, quote, page=None, paragraph=None):
        position = positions[source]
        source_text = bundle.sources[position].text
        start, end = anchors.get((source, page, paragraph), (0, len(source_text)))
        match = source_text.find(quote, start, end)
        if match < 0:
            # The engine allows whitespace normalization when validating a quotation.
            pattern = r"\s+".join(re.escape(word) for word in quote.split())
            found = re.search(pattern, source_text[start:end]) if pattern else None
            if found is None:
                return None
            match, finish = start + found.start(), start + found.end()
        else:
            finish = match + len(quote)
        return {**bundle.excerpt(position, match, finish), "page": page, "paragraph": paragraph}

    modules = {
        row["key"]: dict(**row, defects=[], ok=True, error=None, status_code=200, criteria=[],
                         elapsed_sec=timings.get(row["key"]))
        for row in agent_list(mode, language)
    }
    if partial:
        modules["logic"].update(ok=False, error=result.auditor_failure, status_code=502)
    for finding_id, original in originals.items():
        decision = decisions.get(finding_id)
        if decision and decision.decision not in ("confirmed", "revised"):
            continue
        ids = decision.requirement_ids if decision else original["requirement_ids"]
        severity = (decision.final_severity if decision else original["severity"]).title()
        evidence = [item.model_dump() for item in decision.final_evidence] if decision and decision.final_evidence else original["evidence"]
        if isinstance(evidence, str):
            req = requirements[original["requirement_id"]]
            evidence = [dict(source=req.source, quote=evidence, page=req.page, paragraph=req.paragraph)]
        proofs = [p for item in evidence
                  if (p := point(item["source"], item["quote"], item.get("page"), item.get("paragraph")))]
        candidate = finding_id.startswith("REVIEW-")
        reason = "human_review" if candidate else None
        criterion = original.get("category") or original["criterion"]
        recommendation = decision.recommendation if decision else original["recommendation"]
        row = dict(id=finding_id, title=(decision.title if decision else original.get("title")) or criterion,
                   severity=severity, criterion_id=criterion, criterion={"id": criterion, "title": criterion},
                   requirement_ids=ids, evidence=proofs, quote=proofs[0]["text"] if proofs else "",
                   issue=decision.explanation if decision else original["explanation"], suggestion=recommendation,
                   confidence=decision.confidence if decision else original["confidence"],
                   clarification=recommendation, verified="exact" if proofs else "unverified",
                   review_reason=reason, score_points=0, references=[], also_by=[],
                   audit_decision=decision.decision if decision else None,
                   audit_reason=decision.audit_reason if decision else None)
        # Native recommendations are advisory. A direct replacement requires an exact,
        # uniquely located line, explicitly returned as a proposed rewrite, plus human approval.
        rewrite = (not partial and not candidate and len(proofs) == 1 and len(ids) == 1
                   and proofs[0]["text"] == requirements[ids[0]].text
                   and "\n" not in recommendation and "\n" not in proofs[0]["text"]
                   and bundle.sources[positions[requirements[ids[0]].source]].text.count(proofs[0]["text"]) == 1
                   and recommendation.startswith("REWRITE: "))
        if rewrite:
            row.update(suggestion=recommendation.removeprefix("REWRITE: ").strip(), fix_kind="rewrite")
        row["fix"] = _fix_plan(row)
        key = "quick" if not deep else "kyc_aml" if original.get("agent") == "quality_analyst" else "security"
        modules[key]["defects"].append(row)

    findings = [row for module in modules.values() for row in module["defects"]]
    active = [row for row in findings if not row["review_reason"]]
    distribution = Counter(row["severity"] for row in active)
    for req in requirements.values():
        proof = point(req.source, req.text, req.page, req.paragraph)
        linked = [row for row in findings if req.id in row["requirement_ids"]]
        assessment = assessments[req.id]
        severity = next((level for level in SEVERITIES
                         if any(row["severity"] == level and not row["review_reason"] for row in linked)), None)
        report.setdefault("native_requirements", []).append({
            **(proof or {}), **req.model_dump(), "source_id": mappings[req.id]["original_id"],
            "section": mappings[req.id].get("section", ""), "kind": req.type, "priority": None, "severity": severity,
            "finding_ids": [row["id"] for row in linked], "risk_count": sum(not row["review_reason"] for row in linked),
            "status": "risk" if any(not row["review_reason"] for row in linked) else "review" if linked else "clear",
            "assessment": assessment.model_dump(),
        })
    score = round(mean(row.quality_score for row in assessments.values())) if assessments and not bundle.warnings and not partial else None
    counts = {severity: distribution[severity] for severity in SEVERITIES}
    readiness = result.audit.readiness if audited else None
    if readiness == "ready" and bundle.warnings:
        readiness = "conditionally_ready"
    score_status = ("incomplete" if bundle.warnings or partial else "critical" if readiness == "not_ready"
                    else "review" if active or readiness == "conditionally_ready" else "clear")
    report.update(mode="live", provider="Gemini", model=result.passport["configured_model"], score=score,
                  score_status=score_status,
                  partial=partial or bool(bundle.warnings), modules=list(modules.values()), counts=counts,
                  scored_counts=dict.fromkeys(SEVERITIES, 0) if partial else counts,
                  failed_agents=["logic"] if partial else [],
                  auditor_failure=result.auditor_failure if partial else None,
                  independent_audit=audited,
                  pending_findings=len(findings) - len(active), readiness=readiness,
                  confirmed_count=len(active) if audited else None, preliminary_count=None if audited else len(active),
                  native=result.model_dump(mode="json"), source_warnings=list(bundle.warnings),
                  knowledge_graph=json.loads(graph_json(build_knowledge_graph(result))) if audited else None)
    report["document"]["requirements"] = report.pop("native_requirements", [])
    report["insights"] = build_insights(bundle, findings, settings.profile, mode, language)
    report["coverage"].update(agents_completed=sum(module["ok"] for module in modules.values()), agents_total=len(modules),
                              criteria_in_completed_agents=0 if partial else len(CRITERIA), criteria_total=len(CRITERIA))
    report["check"].update(criteria=list(CRITERIA), completed_criteria=[] if partial else list(CRITERIA), minimum_severity="Low")
    return report
