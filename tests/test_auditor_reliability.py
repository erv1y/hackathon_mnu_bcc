import io
import json
import os
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from google.genai.errors import ClientError

from agents import auditor
from core import llm_client
from core.llm_client import GeminiRequestError
from core.models import AuditMergePlan, AuditPatch, AuditResult
from test_auditor_v08 import fixtures


class AuditorReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.extraction, self.sections, self.quality, self.consistency, self.result = fixtures()
        self.findings = auditor.prepare_findings(self.quality, self.consistency)
        self.enterContext(patch.dict(os.environ, {"AUDIT_BATCH_SIZE": "12"}))

    def audit(self):
        return auditor.audit_results(self.sections, self.extraction, self.quality, self.consistency, {"complete": True})

    def test_reports_all_violated_rules_with_trusted_ids(self):
        def no_decision(rows):
            rows.pop(2)

        def duplicate(rows):
            rows.append(rows[2].model_copy())

        cases = [
            (no_decision, "missing_decision", "CON-002"),
            (duplicate, "duplicate_decision", "CON-002"),
            (lambda rows: setattr(rows[1].final_evidence[0], "quote", "secret source excerpt"), "invalid_quotation", "CON-001"),
            (lambda rows: setattr(rows[1].final_evidence[0], "paragraph", 99), "invalid_source_coordinates", "CON-001"),
            (lambda rows: setattr(rows[0], "merged_into", "MISSING-999"), "invalid_merge_target", "QUAL-001"),
            (lambda rows: setattr(rows[0], "root_cause", "distinct issue"), "merge_root_cause_or_scope_mismatch", "QUAL-001"),
            (lambda rows: setattr(rows[0], "scope", "distinct scope"), "merge_root_cause_or_scope_mismatch", "QUAL-001"),
            (lambda rows: setattr(rows[0], "final_severity", "high"), "invalid_severity", "QUAL-001"),
            (lambda rows: rows[0].requirement_ids.append("R1"), "invalid_requirement_ids", "QUAL-001"),
        ]
        for mutate, rule, finding_id in cases:
            with self.subTest(rule=rule):
                candidate = self.result.model_copy(deep=True)
                mutate(candidate.decisions)
                with self.assertRaises(auditor.AuditValidationError) as error:
                    auditor.validate_audit(candidate, self.findings, self.extraction)
                self.assertIn((rule, finding_id), [(issue.rule, issue.finding_id) for issue in error.exception.issues])
                self.assertNotIn("secret source", str(error.exception))

    def test_targeted_correction_locks_other_decisions_and_sanitizes_diagnostics(self):
        damaged = self.result.model_dump()
        damaged["decisions"][0].pop("finding_id")
        damaged["decisions"][1]["final_evidence"][0]["quote"] = "private fabricated quotation"
        damaged["decisions"].append({"finding_id": "secret unknown label", "recommendation": "must not appear"})
        changed = self.result.decisions[2].model_copy(update={"title": "Unsolicited replacement"})
        correction = AuditPatch(decisions=[*self.result.decisions[:2], changed])
        with patch.object(auditor, "ask_gemini", side_effect=[json.dumps(damaged), correction.model_dump_json()]) as model:
            with self.assertLogs("agents.auditor", level="WARNING") as captured:
                got = self.audit()
        request = json.loads(model.call_args.kwargs["prompt"])
        self.assertCountEqual(request["requested_finding_ids"], ["QUAL-001", "CON-001"])
        self.assertEqual([row["finding_id"] for row in request["locked_decisions"]], ["CON-002"])
        self.assertNotIn("private fabricated quotation", str(request))
        self.assertEqual(got.decisions, self.result.decisions)
        logs = "\n".join(captured.output)
        for expected in ("attempt=1/3", "missing_decision finding=QUAL-001",
                         "invalid_quotation finding=CON-001", "unexpected_finding_id"):
            self.assertIn(expected, logs)
        for secret in ("private fabricated quotation", "secret unknown label", "must not appear"):
            self.assertNotIn(secret, logs)

    def test_duplicate_and_invalid_severity_try_stronger_model_on_third_attempt(self):
        initial = self.result.model_dump()
        initial["decisions"].append(initial["decisions"][1])
        invalid_patch = self.result.decisions[1].model_dump()
        invalid_patch["final_severity"] = "unexpected private text"
        responses = [
            json.dumps(initial),
            json.dumps({"decisions": [invalid_patch]}),
            AuditPatch(decisions=[self.result.decisions[1]]).model_dump_json(),
        ]
        settings = {"GEMINI_AUDITOR_MODEL": "primary-fixture", "GEMINI_AUDITOR_RETRY_MODEL": "strong-fixture"}
        with patch.dict(os.environ, settings), patch.object(auditor, "ask_gemini", side_effect=responses) as model:
            with self.assertLogs("agents.auditor", level="WARNING") as captured:
                self.assertEqual(self.audit().readiness, "not_ready")
        self.assertEqual([call.kwargs["model"] for call in model.call_args_list],
                         ["primary-fixture", "primary-fixture", "strong-fixture"])
        self.assertIn("invalid_severity finding=CON-001", "\n".join(captured.output))
        self.assertNotIn("unexpected private text", "\n".join(captured.output))

    def test_unavailable_retry_model_keeps_provider_fallback(self):
        client = MagicMock()
        client.models.generate_content.side_effect = [
            ClientError(404, {"error": {"message": "private provider detail"}}),
            SimpleNamespace(text="{}", usage_metadata=None, model_version=None),
        ]
        factory = MagicMock()
        factory.return_value.__enter__.return_value = client
        output = io.StringIO()
        with patch.multiple(llm_client, API_KEY="test-only-key", MODEL_NAME="available-model"), patch(
            "core.llm_client.genai.Client", factory
        ), redirect_stdout(output):
            self.assertEqual(llm_client.ask_gemini("fixture", model="unavailable-retry-model"), "{}")
        models = [call.kwargs["model"] for call in client.models.generate_content.call_args_list]
        self.assertEqual(models, ["unavailable-retry-model", "available-model"])
        self.assertNotIn("private provider detail", output.getvalue())
        self.assertNotIn("test-only-key", output.getvalue())

    def test_cross_batch_semantic_merge_preserves_ids_and_rejects_invalid_plan(self):
        result = self.result.model_copy(deep=True)
        member = result.decisions[0]
        member.decision, member.final_severity, member.merged_into = "confirmed", "high", None
        member.root_cause = member.root_cause.lower()
        first = result.model_copy(update={"decisions": [member]})
        next_batches = [AuditPatch(decisions=[row]).model_dump_json() for row in result.decisions[1:]]
        proposed = {"finding_id": "QUAL-001", "merged_into": "CON-001", "audit_reason": "Same lookup defect"}
        bad = {**proposed, "merged_into": "CON-002"}
        responses = [first.model_dump_json(), *next_batches, json.dumps({"merges": [bad]}),
                     json.dumps({"merges": [proposed]})]
        with patch.dict(os.environ, {"AUDIT_BATCH_SIZE": "1"}), patch.object(auditor, "ask_gemini", side_effect=responses) as model:
            got = self.audit()
        self.assertEqual([call.kwargs["response_schema"] for call in model.call_args_list],
                         [AuditResult, AuditPatch, AuditPatch, AuditMergePlan, AuditMergePlan])
        self.assertEqual(got.decisions[0].merged_into, "CON-001")
        self.assertEqual(got.decisions[0].root_cause, got.decisions[1].root_cause)
        self.assertEqual([row.finding_id for row in got.decisions], ["QUAL-001", "CON-001", "CON-002"])
        auditor.validate_audit(got, self.findings, self.extraction)

    def test_exhaustion_distinguishes_validation_and_provider_failures(self):
        choices = [
            ("invalid JSON SECRET", None, "auditor_validation_failed", 3),
            (None, GeminiRequestError("private SDK detail", 429), "auditor_provider_failed", 1),
        ]
        for response, exc, code, count in choices:
            with self.subTest(code=code), patch.object(auditor, "ask_gemini", return_value=response, side_effect=exc) as model:
                with self.assertRaises(auditor.AuditorFailure) as failure:
                    self.audit()
                self.assertEqual(failure.exception.code, code)
                self.assertEqual(model.call_count, count)
                self.assertNotIn("private", str(failure.exception))
