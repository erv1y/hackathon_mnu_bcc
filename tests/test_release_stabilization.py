import io
import json
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from agents import consistency_analyst
from core.llm_client import retry_after
from core.models import ExtractionResult
import evaluate_graph
from tests.test_graph_diagnostics import case, diagnostic_case, finding, report


class StabilizationTests(unittest.TestCase):
    def test_candidate_repair_keeps_valid_rows_and_marks_unresolved(self):
        extraction = ExtractionResult(requirements=[
            dict(id=f"R{i}", type="functional", text=f"Source requirement {i}.", source="fixture.md")
            for i in (1, 2)
        ])
        bad = dict(category="contradiction", severity="high", requirement_ids=["R1"],
                   title="Conflicting obligations", explanation="The obligations disagree.",
                   impact="Conflicting outcomes", recommendation="Clarify policy", confidence=.8)
        good = dict(bad, category="missing_requirement", title="Undefined outcome")
        envelope = lambda rows: json.dumps({"findings": rows, "summary": "Reviewed"})
        first = envelope([good, bad])
        for repaired in (True, False):
            resolution = dict(candidate_index=1, disposition="repaired", reason="Both source IDs support the candidate.",
                              candidate=dict(bad, requirement_ids=["R1", "R2"]))
            correction = json.dumps({"resolutions": [resolution] if repaired else [], "summary": "Reviewed"})
            with patch.object(consistency_analyst, "ask_gemini", side_effect=[first, correction, envelope([])]) as api:
                result = consistency_analyst.analyze_consistency(extraction)
                payload = json.loads(api.call_args_list[1].kwargs["prompt"])
            self.assertEqual([row["candidate_index"] for row in payload["repair"]["requested"]], [1])
            self.assertEqual(result.findings[0].title, "Undefined outcome")
            self.assertEqual(len(result.findings), 2 if repaired else 1)
            self.assertEqual(bool(result.unresolved), not repaired)

    def test_canonical_classification_retains_historical_score(self):
        item = finding(criterion="verifiability")
        item["original_criterion_id"] = "unambiguity"
        scored = diagnostic_case(case(), report(item))
        self.assertEqual(scored["strict"]["matched"], 0)
        self.assertEqual(scored["diagnostic_counts"]["criterion_exact"], 1)

    def test_resume_isolates_failures_and_rejects_revisions(self):
        with TemporaryDirectory() as root:
            directory = Path(root)
            dataset = directory / "suite.json"
            other = case().model_copy(update={"id": "failed", "scope": "Settlement only"})
            dataset.write_text(json.dumps({"cases": [case().model_dump(), other.model_dump()]}))
            args = ["evaluate_graph", "--dataset", str(dataset), "--diagnostics-dir", str(directory)]
            with patch.object(evaluate_graph, "API_KEY", "fixture"), \
                 patch.object(evaluate_graph, "revision", return_value={"commit": "first", "dirty": False}) as revision, \
                 patch.object(evaluate_graph, "analyze", side_effect=[report(finding()), ValueError("secret contents"),
                     report(finding()), report(finding())]) as analyze, redirect_stderr(io.StringIO()):
                output = io.StringIO()
                with patch("sys.argv", args), redirect_stdout(output):
                    evaluate_graph.main()
                self.assertEqual(json.loads(output.getvalue())["run_counts"]["failed"], 1)
                self.assertNotIn("secret", (directory / "failed.case.json").read_text())
                self.assertIn("Settlement only", analyze.call_args.args[4])
                self.assertNotIn("expected", analyze.call_args.args[4])
                with patch("sys.argv", args + ["--resume"]), redirect_stdout(io.StringIO()):
                    evaluate_graph.main()
                self.assertEqual(analyze.call_count, 3)
                revision.return_value = {"commit": "different", "dirty": False}
                with patch("sys.argv", args + ["--resume"]), self.assertRaises(SystemExit):
                    evaluate_graph.main()
                self.assertEqual(analyze.call_count, 3)

    def test_retry_after_reads_provider_header(self):
        error = type("Error", (), {"response": type("Response", (), {"headers": {"Retry-After": "7"}})()})()
        self.assertEqual(retry_after(error), 7)

    def test_auditor_repair_supplies_known_source_only(self):
        from agents import auditor
        from core.models import AuditPatch
        from test_auditor_v08 import fixtures

        extraction, sections, quality, consistency, result = fixtures()
        damaged = result.model_copy(deep=True)
        damaged.decisions[0].explanation += " Also conflicts with R3."
        repaired = AuditPatch(decisions=[result.decisions[0]]).model_dump_json()
        with patch.object(auditor, "ask_gemini", side_effect=[damaged.model_dump_json(), repaired]) as api:
            audited = auditor.audit_results(sections, extraction, quality, consistency, {"complete": True})
        payload = json.loads(api.call_args.kwargs["prompt"])
        self.assertEqual([row["id"] for row in payload["evidence_repairs"]["QUAL-001"]], ["R3"])
        self.assertEqual(audited.decisions[0].explanation, result.decisions[0].explanation)

    def test_grounded_withdrawal_and_bad_indices_preserve_coverage_limit(self):
        req = dict(id="R1", type="functional", text="Export the account statement.",
                   source="fixture.md", paragraph=1)
        extraction = ExtractionResult(requirements=[req])
        candidate = dict(category="duplicate", severity="low", requirement_ids=["R1"],
                         title="Duplicate report", explanation="Only one ID was provided.",
                         impact="Repeated wording", recommendation="Check the obligation", confidence=.8)
        good = dict(candidate, category="missing_dependency", title="Missing policy")
        withdrawn = dict(candidate_index=0, disposition="withdrawn",
                         reason="The only cited requirement contains no second obligation.",
                         evidence=[dict(requirement_id="R1", quote=req["text"],
                                        source=req["source"], paragraph=1)])
        invalid = dict(withdrawn, candidate_index=42)
        for resolutions, code in (([withdrawn], "withdrawn_candidate"),
                                  ([invalid], "unresolved_candidates"),
                                  ([withdrawn, withdrawn], "unresolved_candidates")):
            with self.subTest(resolutions=resolutions):
                responses = [dict(findings=[candidate, good], summary="Reviewed"),
                             dict(resolutions=resolutions, summary="Repaired"),
                             dict(findings=[], summary="No further findings")]
                with patch.object(consistency_analyst, "ask_gemini",
                                  side_effect=[json.dumps(response) for response in responses]):
                    result = consistency_analyst.analyze_consistency(extraction)
                self.assertEqual(result.unresolved[0]["code"], code)
                self.assertEqual([row.category for row in result.findings], ["missing_dependency"])
                self.assertEqual(any(row.get("disposition") == "withdrawn" for row in result.repair_diagnostics),
                                 code == "withdrawn_candidate")
