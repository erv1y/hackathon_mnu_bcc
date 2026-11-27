import io
import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from docx import Document
from fastapi.testclient import TestClient

import main
import orchestrator
import web
from agents.quick_validator import QuickValidationResult
from core.models import AuditResult, ConsistencyAnalysisResult, ExtractionResult, QualityAnalysisResult
import pymupdf


class GraphWebTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.multiple(web, API_KEY="mock-key", DEMO_ONLY=False))
        self.http = self.enterContext(TestClient(web.app))

    def test_deep_upload_runs_parallel_nodes_and_exports_native_report(self):
        pdf_quote = "The daily transfer limit is 500 USD per account."
        docx_quote = "The daily transfer limit is 100 USD per account."
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((72, 72), pdf_quote)
            pdf_bytes = pdf.tobytes()
        doc = Document()
        doc.add_paragraph(docx_quote)
        docx = io.BytesIO()
        doc.save(docx)
        sync = threading.Barrier(2, timeout=5)
        completed = []

        def extract(sections):
            section = next(s for s in sections if s["text"])
            return ExtractionResult(requirements=[dict(
                id="R1", type="business_rule", **{key: section[key] for key in ("source", "text", "page", "paragraph")}
            )])

        def quality(extraction, _sections):
            sync.wait()
            completed.append("quality")
            return QualityAnalysisResult(assessments=[
                dict(requirement_id=r.id, quality_score=90, status="passed", findings=[])
                for r in extraction.requirements
            ])

        def consistency(extraction, _sections):
            sync.wait()
            completed.append("consistency")
            return ConsistencyAnalysisResult(summary="Conflicting limits", findings=[dict(
                id="CON-001", category="contradiction", severity="high",
                requirement_ids=[r.id for r in extraction.requirements], title="Conflicting limits",
                evidence=[dict(requirement_id=r.id, quote=r.text, source=r.source, page=r.page,
                               paragraph=r.paragraph) for r in extraction.requirements],
                explanation="Limits disagree", impact="Affects transfers",
                recommendation="Which limit is authoritative?", confidence=.95,
            )])

        def auditor(_sections, _extraction, _quality, findings, _logic):
            self.assertCountEqual(completed, ["quality", "consistency"])
            finding = next(f for f in findings.findings if f.id == "CON-001")
            return AuditResult(
                extraction_audit=dict(status="complete", missing_requirements=[], unsupported_requirements=[], comments=""),
                decisions=[dict(finding_id=finding.id, decision="revised", final_severity="critical",
                                requirement_ids=finding.requirement_ids, title=finding.title, explanation=finding.explanation,
                                recommendation=finding.recommendation, audit_reason="Both limits apply to the same account.",
                                confidence=.95, root_cause="Conflicting limits", scope="Account transfers", final_evidence=finding.evidence)],
                readiness="not_ready", executive_summary="Уточнить лимит", priority_actions=[], limitations=[],
            )

        with patch.multiple(orchestrator, extract_requirements=extract, analyze_quality=quality,
                            analyze_consistency=consistency, audit_results=auditor,
                            analyze_counterexamples=lambda _e, result, *_: result,
                            augment_consistency=lambda _e, result, *_: result):
            response = self.http.post("/api/audit/stream", data={"consent": "true", "language": "en"},
                                      files=[("files", ("limits.pdf", pdf_bytes)),
                                             ("files", ("policy.docx", docx.getvalue()))])
        self.assertEqual(response.status_code, 200, response.text)
        events = [json.loads(line) for line in response.text.splitlines() if line]
        self.assertEqual(events[-1]["type"], "result", events)
        statuses = [(event["type"], event.get("key")) for event in events[:-1]]
        self.assertLess(statuses.index(("agent_done", "payments")), statuses.index(("agent_start", "kyc_aml")))
        for key in ("kyc_aml", "security"):
            self.assertLess(statuses.index(("agent_start", key)), statuses.index(("agent_done", key)))
            self.assertLess(statuses.index(("agent_done", key)), statuses.index(("agent_start", "logic")))
        report = events[-1]
        self.assertEqual((report["confirmed_count"], report["readiness"], report["score"]), (1, "not_ready", 90))
        self.assertEqual(report["counts"]["Critical"], 1)
        finding = report["modules"][2]["defects"][0]
        self.assertEqual(finding["id"], "CON-001")
        self.assertEqual([(p["file_name"], p["page"], p["paragraph"]) for p in finding["evidence"]],
                         [("limits.pdf", 1, None), ("policy.docx", None, 1)])
        self.assertEqual(len(report["knowledge_graph"]["edges"]), 1)
        pdf = self.http.post("/api/audit/pdf", json=report["native"])
        self.assertEqual(pdf.status_code, 200, pdf.text if pdf.status_code != 200 else "")
        self.assertTrue(pdf.content.startswith(b"%PDF-"))
        executive = self.http.post("/api/audit/pdf?executive=true", json=report["native"])
        self.assertEqual(executive.status_code, 200)
        with pymupdf.open(stream=executive.content, filetype="pdf") as doc:
            self.assertIn(len(doc), (2, 3))
            self.assertIn("CON-001", "".join(page.get_text() for page in doc))
        metrics = report["native"]["passport"]["audit_metrics"]
        for key in ("score", "score_formula", "counts", "merged_duplicates"):
            self.assertEqual(report[key], metrics[key])
        self.assertNotIn("score_penalty", report)
        self.assertEqual(finding["action_kind"], "business_decision")
        self.assertCountEqual([p["requirement_id"] for p in finding["evidence"]], finding["requirement_ids"])
        qa = self.http.post("/api/audit/ask", json={
            "intent": "explain", "focus_id": finding["id"],
            "documents": [{"name": "limits.pdf", "text": pdf_quote},
                          {"name": "policy.docx", "text": docx_quote, "role": "appendix"}],
            "findings": [{**{key: finding[key] for key in ("id", "title", "issue", "suggestion", "severity")},
                          "quotes": [pdf_quote, docx_quote], "file_ids": ["D1", "D2"]}],
        })
        self.assertEqual([proof["text"] for proof in qa.json()["items"][0]["evidence"]], [pdf_quote, docx_quote])

    def test_quick_has_no_auditor_or_readiness_claim(self):
        text = "The service records successful transfers once per unique operation."
        quick = QuickValidationResult(
            extraction={"requirements": [{"id": "R1", "type": "functional", "text": text,
                                           "source": "specification.md", "paragraph": 1}]},
            quality={"assessments": [{"requirement_id": "R1", "quality_score": 80, "status": "passed", "findings": []}]},
            consistency={"findings": [], "summary": "No preliminary findings"},
        )
        with patch.object(orchestrator, "quick_validate", return_value=quick), patch.object(orchestrator, "audit_results") as auditor:
            response = self.http.post("/api/audit/stream", data={"text": text, "mode": "quick", "consent": "true"})
        events = [json.loads(line) for line in response.text.splitlines() if line]
        self.assertEqual(events[-1]["type"], "result", events)
        self.assertIsNone(events[-1]["readiness"])
        self.assertFalse(events[-1]["native"]["independent_audit"])
        self.assertEqual([event["key"] for event in events if event["type"] == "agent_start"], ["quick"])
        auditor.assert_not_called()

    def test_cache_hit_does_not_emit_agent_completion_and_cli_still_exports(self):
        text = "The service records a transfer once for any unique operation identifier."
        result = QuickValidationResult(
            extraction={"requirements": [{"id": "R1", "type": "functional", "text": text,
                                           "source": "specification.md", "paragraph": 1}]},
            quality={"assessments": [{"requirement_id": "R1", "quality_score": 80, "status": "passed", "findings": []}]},
            consistency={"findings": [], "summary": "No preliminary findings"},
        )
        with TemporaryDirectory() as folder:
            root = Path(folder)
            params = {"text": text, "mode": "quick", "consent": "true", "cache": "true"}
            with patch.object(orchestrator, "CACHE_DIR", root / "cache"), patch.object(
                orchestrator, "quick_validate", return_value=result
            ) as validator:
                first = self.http.post("/api/audit/stream", data=params)
                second = self.http.post("/api/audit/stream", data=params)
                self.assertEqual(validator.call_count, 1)
                self.assertIn("agent_start", first.text)
                events = [json.loads(line) for line in second.text.splitlines() if line]
                self.assertEqual(events[-1]["native"]["passport"]["run_kind"], "cache_hit")
                self.assertFalse(any(event["type"].startswith("agent_") for event in events[:-1]))
                source, output = root / "specification.md", root / "report.pdf"
                source.write_text(text, encoding="utf-8")
                with patch("sys.argv", ["main.py", "--input", str(source), "--output", str(output), "--mode", "quick"]):
                    self.assertEqual(main.main(), 0)
                self.assertTrue(output.read_bytes().startswith(b"%PDF-"))
                self.assertTrue((root / "quick_validation.json").is_file())

    def test_failed_node_emits_error_without_completed_report(self):
        with patch.object(orchestrator, "quick_validate", side_effect=RuntimeError("private data")):
            response = self.http.post("/api/audit/stream", data={
                "text": "A transfer requires a valid account and a unique operation ID.", "mode": "quick", "consent": "true",
            })
        self.assertNotIn("private data", response.text)
        events = [json.loads(line) for line in response.text.splitlines() if line]
        self.assertEqual(events[-1]["type"], "error")
        self.assertFalse(next(event["ok"] for event in events if event["type"] == "agent_done"))
