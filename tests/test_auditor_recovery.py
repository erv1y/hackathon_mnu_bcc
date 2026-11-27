import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import pymupdf

from agents import auditor
from core.models import AuditResult, ConsistencyAnalysisResult, ExtractionResult, QualityAnalysisResult
import orchestrator
import web
from web_engine import AuditRetries


TEXT = "The service must process settlement within one business day."


class AuditorRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch.multiple(web, API_KEY="mock-key", DEMO_ONLY=False))
        self.enterContext(patch("web_engine.AUDIT_RETRIES", AuditRetries(limit=2)))
        self.enterContext(patch.object(orchestrator, "CACHE_DIR", self.root / "checkpoints"))
        self.http = self.enterContext(TestClient(web.app))
        self.request = {"text": TEXT, "consent": "true", "mode": "deep"}
        self.result = AuditResult(
            extraction_audit=dict(status="complete", missing_requirements=[], unsupported_requirements=[], comments="reviewed"),
            readiness="ready", executive_summary="Checked", priority_actions=[], limitations=[],
            decisions=[dict(finding_id="QUAL-001", decision="rejected", requirement_ids=["R1"], title="Timing",
                            explanation="Already testable", recommendation="Keep wording",
                            audit_reason="A concrete duration is specified", confidence=.9)],
        )
        self.extraction = ExtractionResult(requirements=[dict(
            id="R1", type="functional", text=TEXT, source="specification.md", paragraph=1,
        )])
        self.quality = QualityAnalysisResult(assessments=[dict(
            requirement_id="R1", status="needs_revision", quality_score=80, findings=[dict(
                requirement_id="R1", criterion="verifiability", evidence=TEXT, severity="medium",
                confidence=.8, explanation="Check the deadline", recommendation="Clarify timing",
            )],
        )])
        self.consistency = ConsistencyAnalysisResult(summary="Complete", findings=[])
        self.nodes = {}
        for name, value in [("extract_requirements", self.extraction), ("analyze_quality", self.quality),
                            ("analyze_consistency", self.consistency)]:
            self.nodes[name] = self.enterContext(patch.object(orchestrator, name, return_value=value))
        def counterexamples(_extraction, consistency, _sections, logic):
            logic["complete"] = True
            return consistency

        self.nodes["analyze_counterexamples"] = self.enterContext(
            patch.object(orchestrator, "analyze_counterexamples", side_effect=counterexamples)
        )

    def stream(self, **extra):
        response = self.http.post("/api/audit/stream", data={**self.request, **extra})
        self.assertEqual(response.status_code, 200, response.text)
        return [json.loads(line) for line in response.text.splitlines() if line]

    def assert_partial(self, events):
        report = events[-1]
        self.assertEqual(report["type"], "result", events)
        self.assertEqual(report["auditor_failure"], "auditor_validation_failed")
        self.assertFalse(report["independent_audit"])
        self.assertTrue(report["partial"])
        self.assertIsNone(report["readiness"])
        self.assertIsNone(report["confirmed_count"])
        self.assertIsNone(report["score"])
        self.assertEqual(report["preliminary_count"], 1)
        self.assertNotIn("audit", report["native"])
        self.assertEqual(report["coverage"]["agents_completed"], 3)
        stages = [row for row in events if row.get("key") == "logic" and row["type"] == "agent_done"]
        self.assertEqual(stages, [{"type": "agent_done", "key": "logic", "ok": False, "code": "auditor_validation_failed"}])
        self.assertNotIn("validating", [row["type"] for row in events])
        self.assertEqual(report["modules"][1]["defects"][0]["quote"], TEXT)
        return report

    def assert_recovered(self, events):
        result = events[-1]
        self.assertEqual((result["type"], result["native"]["passport"]["run_kind"]), ("result", "resume"), events)
        self.assertTrue(result["independent_audit"])
        self.assertEqual(result["coverage"]["agents_completed"], 4)
        self.assertEqual([row["key"] for row in events if row["type"] == "agent_start"], ["logic"])
        self.assertCountEqual([row["key"] for row in events if row["type"] == "agent_reused"],
                              ["payments", "kyc_aml", "security"])
        for node in self.nodes.values():
            node.assert_called_once()

    def test_partial_report_and_in_memory_retry_with_exact_input(self):
        with patch.object(auditor, "ask_gemini", side_effect=["{}"] * 3 + [self.result.model_dump_json()]):
            report = self.assert_partial(self.stream())
            token = report["auditor_retry"]["token"]
            self.assertNotIn(token, json.dumps(report["native"]))
            mismatch = self.stream(text=TEXT + " New material.", retry_token=token)
            self.assertEqual(mismatch[-1]["status"], 409)
            self.assert_recovered(self.stream(retry_token=token))
            self.assertEqual(self.stream(retry_token=token)[-1]["status"], 409)
        self.assertFalse((self.root / "checkpoints").exists())
        pdf = self.http.post("/api/audit/pdf", json=report["native"])
        self.assertEqual(pdf.status_code, 200)
        with pymupdf.open(stream=pdf.content) as document:
            pdf_text = " ".join(document[0].get_text().split())
            self.assertIn("частичный отчёт", pdf_text)
            self.assertIn("Готовность не оценена", pdf_text)

    def test_disk_checkpoint_requires_consent_and_resumes_after_failure(self):
        with patch.object(auditor, "ask_gemini", side_effect=["{}"] * 3 + [self.result.model_dump_json()]):
            report = self.assert_partial(self.stream(cache="true"))
            self.assertEqual(report["auditor_retry"], {"cache": True})
            self.assertEqual(len(list((self.root / "checkpoints").glob("*.sqlite"))), 1)
            self.assert_recovered(self.stream(cache="true"))

    def test_incomplete_consistency_keeps_quality_and_never_starts_model_audit(self):
        self.consistency.unresolved = [{"phase": "global", "code": "unresolved_candidates", "candidate_indices": [2]}]
        with patch.object(orchestrator, "audit_results") as audit:
            events = self.stream()
        audit.assert_not_called()
        report = events[-1]
        self.assertEqual(report["auditor_failure"], "consistency_incomplete")
        self.assertEqual(report["preliminary_count"], 1)
        self.assertEqual(report["failed_agents"], ["security", "logic"])
        self.assertNotIn("auditor_retry", report)
        self.assertEqual(report["native"]["consistency"]["unresolved"][0]["candidate_indices"], [2])
