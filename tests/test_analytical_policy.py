from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from agents.auditor import AuditIssue
from core.llm_client import ask_gemini, request_language, request_metrics, request_stage


class AnalyticalPolicyTests(unittest.TestCase):
    def test_language_policy_and_model_attempt_metadata(self):
        for language, name in (("en", "English"), ("ru", "Russian"), ("kk", "Kazakh")):
            with self.subTest(language=language):
                metrics = []
                client = MagicMock()
                client.models.generate_content.return_value = SimpleNamespace(text="{}", model_version="fixture-2")
                variables = [(request_language, language), (request_metrics, metrics), (request_stage, "quality")]
                tokens = [(var, var.set(value)) for var, value in variables]
                try:
                    with patch("core.llm_client.API_KEY", "fixture"), patch("core.llm_client.genai.Client") as factory:
                        factory.return_value.__enter__.return_value = client
                        self.assertEqual(ask_gemini("Source quotation"), "{}")
                finally:
                    for var, token in reversed(tokens):
                        var.reset(token)
                kwargs = client.models.generate_content.call_args.kwargs
                instruction = kwargs["config"].system_instruction
                self.assertIn(f"generated display prose in {name}", instruction)
                self.assertIn("Critical requires direct", instruction)
                self.assertIn("missing_dependency for an unavailable reference", instruction)
                self.assertIn("abstraction", instruction)
                self.assertIn("observable acceptance check", instruction)
                self.assertEqual(kwargs["contents"], "Source quotation")
                self.assertEqual(metrics[0]["stage"], "quality")
                self.assertEqual(metrics[0]["status"], "success")
                self.assertNotIn("Source quotation", repr(metrics))

    def test_validation_phases_distinguish_json_schema_and_application(self):
        self.assertEqual(AuditIssue("invalid_json").phase, "schema")
        self.assertEqual(AuditIssue("invalid_severity", origin="schema").phase, "schema")
        self.assertEqual(AuditIssue("invalid_quotation").phase, "application")
