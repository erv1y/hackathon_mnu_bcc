import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from evaluate_graph import GraphCase, main, save_case
from graph_diagnostics import aggregate, diagnostic_case

SOURCE = "Each payout approval must be processed promptly after receipt."
DESCRIPTION = "The payout approval deadline has no measurable upper bound."


def case(*, expected=True, severity="Critical"):
    label = dict(criterion_id="verifiability", severity=severity, evidence="exact",
                 anchors=[SOURCE], description=DESCRIPTION)
    return GraphCase(id="approval", title="Approval", text=SOURCE, expected=[label] if expected else [],
                     reference_requirements=[SOURCE])


def finding(issue=DESCRIPTION, *, criterion="unambiguity", severity="High", evidence=True):
    return dict(issue=issue, criterion_id=criterion, criterion={"id": criterion}, severity=severity,
                evidence=[dict(text=SOURCE, file_name="first.md")] if evidence else [],
                verified="exact" if evidence else "unverified")


def report(*predictions):
    return dict(modules=[{"defects": list(predictions)}], coverage={"agents_completed": 4, "agents_total": 4},
                failed_agents=[], elapsed_sec=3, native=dict(
                    passport={"api_attempts": 5, "reported_tokens": {}},
                    extraction={"requirements": [{"text": SOURCE}]}))


class DiagnosticTests(unittest.TestCase):
    def test_semantics_independent_of_category_and_severity(self):
        item = diagnostic_case(case(), report(finding(), finding("The approving role is missing.")))
        strict, diagnostics = aggregate([item])
        self.assertEqual(strict["recall"], 0)
        self.assertEqual(diagnostics["detection_recall"], 1)
        self.assertEqual(diagnostics["criterion_accuracy"], 0)
        self.assertEqual(diagnostics["exact_severity_accuracy"], 0)
        self.assertEqual(diagnostics["critical_detection_recall"], 1)
        self.assertEqual(diagnostics["critical_exact_recall"], 0)
        self.assertEqual(diagnostics["underestimation_rate"], 1)
        self.assertEqual(diagnostics["severity_confusion"]["Critical"]["High"], 1)
        self.assertEqual(item["matching"][1]["semantic_reason"], "expert_review_required_shared_anchors")

    def test_duplicates_do_not_increase_recall(self):
        item = diagnostic_case(case(), report(finding(), finding(severity="Critical")))
        self.assertEqual(item["diagnostic_counts"]["matched"], 1)
        self.assertEqual(len(item["duplicate_prediction_indices"]), 1)

    def test_missing_evidence_and_expert_review(self):
        result = report(finding(evidence=False, severity="Critical"))
        self.assertEqual(diagnostic_case(case(), result)["diagnostic_counts"]["matched"], 0)
        item = diagnostic_case(case(severity="High"), result, {"0:0": {
            "same_defect": True, "relevant_evidence": False, "recommendation_adequate": False}})
        _, diagnostic = aggregate([item])
        self.assertEqual(diagnostic["overestimation_rate"], 1)
        self.assertEqual(diagnostic["evidence_relevance"], 0)
        self.assertEqual(diagnostic["recommendation_quality"], 0)
        self.assertIsNone(diagnostic["evidence_authenticity"])

    def test_clean_document_and_offline_rescoring(self):
        clean = diagnostic_case(case(expected=False), report())
        self.assertEqual(aggregate([clean])[0]["clean_pass_rate"], 1)
        item = diagnostic_case(case(), report(finding()))
        with TemporaryDirectory() as folder:
            save_case(Path(folder), item, "deep", "fixture-v1")
            output = io.StringIO()
            with patch("evaluate_graph.API_KEY", None), patch("evaluate_graph.analyze") as analyze, \
                 patch("sys.argv", ["evaluate_graph", "--rescore", folder]), redirect_stdout(output):
                main()
            analyze.assert_not_called()
            rescored = json.loads(output.getvalue())
        self.assertEqual(rescored["type"], "offline_rescore")
        self.assertEqual(rescored["metrics"], aggregate([item])[0])
        self.assertEqual(rescored["diagnostic_metrics"], aggregate([item])[1])

    def test_failed_auditor_stays_in_independent_recall_denominator(self):
        complete = report(finding())
        complete["independent_audit"] = True
        partial = report(finding())
        partial.update(partial=True, auditor_retry={"token": "secret"}, failed_agents=["logic"])
        partial["native"]["passport"].update(mode="deep", stage_seconds={"auditor": 2.5},
            model_attempts=[{"model": "backup", "stage": "auditor", "fallback": True}])
        records = [diagnostic_case(case(), item) for item in (complete, partial)]
        metrics = aggregate(records)[1]
        self.assertEqual(metrics["independently_audited_semantic_detection"]["recall"], 0.5)
        self.assertEqual(metrics["preliminary_semantic_detection"]["recall"], 1)
        self.assertEqual(metrics["end_to_end_success_rate"], 0.5)
        self.assertEqual(metrics["incomplete_audit_rate"], 0.5)
        self.assertEqual(metrics["fallback_model_attempts"], {"backup": 1})
        self.assertEqual(metrics["stage_api_attempts"], {"auditor": 1})
        self.assertNotIn("secret", json.dumps(records))

    def test_partial_finding_audit_never_inflates_full_document_recall(self):
        completed = report(finding())
        completed.update(independent_audit=True)
        partial = report(finding())
        partial.update(partial=True, finding_audit_completed=True, failed_agents=["security"])
        partial["native"]["passport"]["mode"] = "deep"
        preliminary = report(finding())
        preliminary.update(partial=True, failed_agents=["logic"])
        preliminary["native"]["passport"]["mode"] = "deep"
        records = [diagnostic_case(case(), row) for row in (completed, partial, preliminary)]
        metrics = aggregate(records)[1]
        self.assertEqual(metrics["partial_finding_audit_detection"]["matched"], 1)
        self.assertEqual(metrics["preliminary_semantic_detection"]["matched"], 1)
        self.assertEqual(metrics["independently_audited_semantic_detection"]["expected"], 3)
        self.assertEqual(metrics["independently_audited_semantic_detection"]["recall"], 0.3333)
        self.assertEqual(metrics["full_document_coverage_rate"], 0.3333)
