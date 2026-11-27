import json
import unittest
from unittest.mock import patch

from google import genai
from google.genai import _transformers

from agents import quick_validator as quick
from core.models import ConsistencyAnalysisResult

TEXT = "The service records each completed transfer once."


def response():
    return {
        "extraction": {"requirements": [
            {"id": "R1", "type": "functional", "text": TEXT, "source": "fixture.md", "paragraph": 1}
        ]},
        "quality": {"assessments": [
            {"requirement_id": "R1", "status": "passed", "quality_score": 100, "findings": []}
        ]},
        "consistency": {"findings": [], "summary": "Preliminary review"},
    }


class QuickSchemaTests(unittest.TestCase):
    sections = [{"source": "fixture.md", "paragraph": 1, "text": TEXT}]

    def test_actual_developer_api_schema_conversion(self):
        with genai.Client(api_key="offline-schema-test", vertexai=False) as client:
            schema = _transformers.t_schema(client._api_client, quick.QuickAgentResponse)
        fields = schema.properties["consistency"].properties
        self.assertEqual(set(fields), {"findings", "summary"})
        self.assertIn("repair_diagnostics", ConsistencyAnalysisResult.model_fields)
        self.assertIn("unresolved", ConsistencyAnalysisResult.model_fields)

    def test_runtime_diagnostics_are_application_owned(self):
        payload = response()
        payload["consistency"].update(unresolved=[{"model": "untrusted"}],
                                      repair_diagnostics=[{"model": "untrusted"}])
        with patch.object(quick, "ask_gemini", return_value=json.dumps(payload)) as ask:
            result = quick.quick_validate(self.sections)
        self.assertIs(ask.call_args.kwargs["response_schema"], quick.QuickAgentResponse)
        self.assertEqual(result.consistency.unresolved, [])
        self.assertEqual(result.consistency.repair_diagnostics, [])
        self.assertEqual(quick.QuickValidationResult.model_validate_json(result.model_dump_json()), result)

    def test_malformed_response_is_retried_without_echo(self):
        with patch.object(quick, "ask_gemini", side_effect=["private-garbage", json.dumps(response())]) as ask:
            with self.assertLogs(quick.logger, "WARNING") as logs:
                result = quick.quick_validate(self.sections)
        self.assertEqual(len(result.extraction.requirements), 1)
        retry = json.loads(ask.call_args.kwargs["prompt"])
        self.assertEqual(retry["correction"]["code"], "invalid_response_schema")
        self.assertNotIn("private-garbage", repr(retry))
        self.assertNotIn("private-garbage", repr(logs.output))

    def test_invalid_evidence_coordinates_and_ids_exhaust_bounded_recovery(self):
        for target, key, value, code in (
            ("requirement", "text", "private-garbage", "unsupported_extraction_evidence"),
            ("requirement", "paragraph", 9, "unsupported_extraction_evidence"),
            ("assessment", "requirement_id", "private-garbage", "invalid_quality_ids_or_evidence"),
        ):
            with self.subTest(target=target, key=key):
                payload = response()
                row = (payload["extraction"]["requirements"][0] if target == "requirement"
                       else payload["quality"]["assessments"][0])
                row[key] = value
                with patch.object(quick, "ask_gemini", return_value=json.dumps(payload)) as ask:
                    with self.assertLogs(quick.logger, "WARNING") as logs:
                        with self.assertRaisesRegex(quick.QuickValidationError, code):
                            quick.quick_validate(self.sections)
                self.assertEqual(ask.call_count, quick.MAX_ATTEMPTS)
                self.assertNotIn("private-garbage", repr(logs.output))

    def test_consistency_evidence_cannot_reference_an_unknown_requirement(self):
        payload = response()
        payload["consistency"]["findings"] = [{
            "id": "CON-001", "category": "missing_dependency", "severity": "medium",
            "requirement_ids": ["R1"], "title": "Missing policy", "explanation": "Policy is absent.",
            "impact": "Implementation blocked", "recommendation": "Provide the policy", "confidence": .8,
            "evidence": [{"requirement_id": "R99", "quote": TEXT, "source": "fixture.md", "paragraph": 1}],
        }]
        with patch.object(quick, "ask_gemini", return_value=json.dumps(payload)) as ask:
            with self.assertLogs(quick.logger, "WARNING"):
                with self.assertRaisesRegex(quick.QuickValidationError, "invalid_consistency_ids_or_evidence"):
                    quick.quick_validate(self.sections)
        self.assertEqual(ask.call_count, quick.MAX_ATTEMPTS)
