import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import pymupdf

from agents import auditor
from core.models import AuditCandidateResult, AuditResult, ConsistencyAnalysisResult, ExtractionResult, FindingEvidence, QualityAnalysisResult
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

    def test_disk_checkpoint_restores_evidence_repair_context(self):
        bad = self.result.model_copy(deep=True)
        row = bad.decisions[0]
        row.decision, row.final_severity = "revised", "medium"
        row.root_cause, row.scope = "Settlement time", "Settlement"
        with patch.object(auditor, "ask_gemini", side_effect=[
            bad.model_dump_json(), auditor.GeminiRequestError("rate limit", 429),
            '{"decisions":[' + self.result.decisions[0].model_dump_json() + ']}',
        ]) as api:
            report = self.stream(cache="true")[-1]
            self.assertEqual(report["audit_coverage"]["remaining_phase"], "finding_review")
            self.assert_recovered(self.stream(cache="true"))
        request = json.loads(api.call_args.kwargs["prompt"])
        context = request["evidence_repairs"]["QUAL-001"]
        self.assertEqual([r["id"] for r in context["verified_sources"]], ["R1"])
        self.assertIn("missing_evidence_ids", {entry["rule"] for entry in context["diagnostics"]})

    def test_dedup_failure_reports_exact_remaining_phase_and_preserves_audit(self):
        findings = self.quality.assessments[0].findings
        findings.append(findings[0].model_copy())
        retained = self.result.decisions[0]
        retained.decision, retained.final_severity = "confirmed", "medium"
        retained.root_cause, retained.scope = "Timing", "Settlement"
        retained.final_evidence = [FindingEvidence(requirement_id="R1", quote=TEXT,
                                                   source="specification.md", paragraph=1)]
        self.result.decisions.append(retained.model_copy(update={"finding_id": "QUAL-002"}))
        with patch.object(auditor, "ask_gemini", side_effect=[
            self.result.model_dump_json(), "{}", "{}", "{}", '{"merges":[]}',
        ]) as api:
            report = self.stream()[-1]
            self.assertTrue(report["partial"])
            self.assertEqual(report["audit_coverage"]["remaining_phase"], "deduplication")
            self.assertEqual(report["audit_coverage"]["validated_count"], 2)
            self.assertIsNone(report["readiness"])
            self.assertEqual(len(report["native"]["finding_audit"]["decisions"]), 2)
            for endpoint in ("/api/audit/pdf", "/api/audit/pdf?executive=true"):
                response = self.http.post(endpoint, json=report["native"])
                self.assertEqual(response.status_code, 200)
                with pymupdf.open(stream=response.content) as pdf:
                    self.assertIn("Незавершённый этап: объединение дубликатов.", " ".join(pdf[0].get_text().split()))
            self.assert_recovered(self.stream(retry_token=report["auditor_retry"]["token"]))
        self.assertIs(api.call_args.kwargs["response_schema"], auditor.AuditMergePlan)
        self.assertEqual(api.call_count, 5)

    def test_incomplete_consistency_still_audits_findings_without_readiness(self):
        self.consistency.unresolved = [{"phase": "global", "code": "unresolved_candidates", "candidate_indices": [2]}]
        with patch.object(auditor, "ask_gemini", return_value=self.result.model_dump_json()) as audit:
            events = self.stream()
        audit.assert_called_once()
        report = events[-1]
        self.assertEqual(report["auditor_failure"], "consistency_incomplete")
        self.assertIsNone(report["preliminary_count"])
        self.assertEqual(report["audited_finding_count"], 0)
        self.assertTrue(report["finding_audit_completed"])
        self.assertFalse(report["independent_audit"])
        self.assertIsNone(report["readiness"])
        self.assertIsNone(report["score"])
        self.assertEqual(report["failed_agents"], ["security"])
        self.assertNotIn("readiness", report["native"]["finding_audit"])
        for url in ("/api/audit/pdf", "/api/audit/pdf?executive=true"):
            pdf = self.http.post(url, json=report["native"])
            self.assertEqual(pdf.status_code, 200)
            with pymupdf.open(stream=pdf.content) as document:
                text = " ".join(document[0].get_text().split())
                self.assertIn("Готовность не оценена", text)
                self.assertIn("Независимо проверенные замечания", text)
        self.assertNotIn("auditor_retry", report)
        self.assertEqual(report["native"]["consistency"]["unresolved"][0]["candidate_indices"], [2])

    def test_partial_finding_audit_can_merge_duplicates(self):
        self.consistency.unresolved = [{"phase": "scenario", "code": "unresolved_candidates",
                                        "candidate_indices": [0]}]
        findings = self.quality.assessments[0].findings
        findings.append(findings[0].model_copy())
        row = self.result.decisions[0]
        row.decision, row.final_severity = "revised", "critical"
        row.root_cause, row.scope = "Same deadline concern", "Settlement"
        row.final_evidence = [FindingEvidence(requirement_id="R1", quote=TEXT,
                                              source="specification.md", paragraph=1)]
        self.result.decisions.append(row.model_copy(update={
            "finding_id": "QUAL-002", "decision": "merged", "final_severity": None,
            "merged_into": row.finding_id,
        }))
        with patch.object(auditor, "ask_gemini", return_value=self.result.model_dump_json()):
            report = self.stream()[-1]
        self.assertTrue(report["partial"])
        self.assertEqual(report["audited_finding_count"], 1)
        self.assertEqual(report["merged_duplicates"], 1)
        self.assertEqual(report["counts"]["Critical"], 1)
        pdf = self.http.post("/api/audit/pdf?executive=true", json=report["native"])
        self.assertEqual(pdf.status_code, 200)
        with pymupdf.open(stream=pdf.content, filetype="pdf") as document:
            text = " ".join(document[0].get_text().split())
        self.assertIn("Критические замечания · 1", text)
        self.assertIn("Готовность не оценена", text)
        self.assertEqual(report["native"]["finding_audit"]["decisions"][1]["merged_into"], "QUAL-001")

    def test_mixed_audit_retains_28_out_of_29_and_resumes_only_the_missing_one(self):
        findings = self.quality.assessments[0].findings
        findings.extend([findings[0].model_copy() for _ in range(28)])
        first = self.result.decisions[0].model_copy(update={
            "decision": "revised", "final_severity": "critical",
            "root_cause": "Risk in the settlement workflow", "scope": "Settlement",
            "final_evidence": [FindingEvidence(requirement_id="R1", source="specification.md",
                                               paragraph=1, quote=TEXT)],
        })
        decisions = {f"QUAL-{i:03d}": self.result.decisions[0].model_copy(update={"finding_id": f"QUAL-{i:03d}"})
                     for i in range(1, 30)}
        decisions[first.finding_id] = first
        batches = []

        def respond(**kwargs):
            payload = json.loads(kwargs["prompt"])
            ids = [row["id"] for row in payload["findings"]]
            batches.append(ids)
            if recovery and ids != ["QUAL-029"]:
                self.fail("Completed audit decisions were requested again")
            rows = [decisions[fid].model_dump(mode="json") for fid in ids]
            if not recovery and "QUAL-029" in ids:
                next(row for row in rows if row["finding_id"] == "QUAL-029")["requirement_ids"] = ["R99"]
            answer = {"decisions": rows}
            if kwargs["response_schema"] is AuditCandidateResult:
                answer.update(self.result.model_dump(mode="json", exclude={"decisions"}))
            return json.dumps(answer)

        recovery = False
        with patch.object(auditor, "ask_gemini", side_effect=respond), patch.dict("os.environ", {"AUDIT_BATCH_SIZE": "12"}):
            partial = self.stream()[-1]
            self.assertTrue(partial["partial"])
            self.assertFalse(partial["finding_audit_completed"])
            self.assertEqual(partial["audit_coverage"]["validated_count"], 28)
            self.assertEqual(partial["audit_coverage"]["unresolved_ids"], ["QUAL-029"])
            self.assertEqual(partial["audited_finding_count"], 1)
            self.assertEqual(partial["preliminary_count"], 1)
            self.assertIsNone(partial["readiness"])
            self.assertIsNone(partial["score"])
            display = {d["id"]: d["audit_status"] for m in partial["modules"] for d in m["defects"]}
            self.assertEqual(display, {"QUAL-001": "audited", "QUAL-029": "preliminary"})
            for suffix in ("", "?executive=true"):
                exported = self.http.post("/api/audit/pdf" + suffix, json=partial["native"])
                self.assertEqual(exported.status_code, 200)
                with pymupdf.open(stream=exported.content, filetype="pdf") as doc:
                    text = " ".join(word for page in doc for word in page.get_text().split())
                    self.assertIn("Критические замечания · 1", text)
                    self.assertIn("Предварительные замечания · 1", text)
            recovery = True
            resumed = self.stream(retry_token=partial["auditor_retry"]["token"])[-1]
        self.assertTrue(resumed["independent_audit"], resumed)
        self.assertEqual(sum(map(len, batches[:3])), 29)
        for node in self.nodes.values():
            node.assert_called_once()

    def test_failing_post_review_keeps_validated_base_audit(self):
        with patch.object(orchestrator, "finalize_review", side_effect=ValueError("enhancement fixture")), patch.object(
            auditor, "ask_gemini", return_value=self.result.model_dump_json()
        ) as ask:
            report = self.stream()[-1]
        self.assertFalse(report["independent_audit"])
        self.assertEqual(report["audit_coverage"]["rejected_ids"], ["QUAL-001"])
        self.assertTrue(report["audit_coverage"]["deduplication_complete"])
        self.assertFalse(report["audit_coverage"]["post_review_complete"])
        with patch.object(auditor, "ask_gemini") as retry:
            recovered = self.stream(retry_token=report["auditor_retry"]["token"])[-1]
        ask.assert_called_once()
        retry.assert_not_called()
        self.assertTrue(recovered["independent_audit"])

    def test_provider_failure_after_first_batch_keeps_validated_work(self):
        from core.llm_client import GeminiRequestError

        findings = self.quality.assessments[0].findings
        findings.extend([findings[0].model_copy(), findings[0].model_copy()])
        with patch.dict("os.environ", {"AUDIT_BATCH_SIZE": "1"}), patch.object(
            auditor, "ask_gemini", side_effect=[self.result.model_dump_json(), GeminiRequestError("timeout")]
        ):
            result = self.stream()[-1]
        self.assertEqual(result["auditor_failure"], "auditor_provider_failed")
        self.assertEqual(result["audit_coverage"]["rejected_ids"], ["QUAL-001"])
        self.assertEqual(result["audit_coverage"]["unresolved_ids"], ["QUAL-002"])
        self.assertEqual(result["audit_coverage"]["not_reviewed_ids"], ["QUAL-003"])
