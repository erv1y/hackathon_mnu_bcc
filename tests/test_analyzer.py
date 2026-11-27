import os
import json
import unittest
from unittest.mock import patch

from analyzer import LLMResponseError, _extract_json, _report_from_payload, describe_llm_error, get_settings
from audit_profile import DEFAULT_PROFILE, load_profile
from prompts import build_system_prompt, build_user_prompt


class ResponseParsingTests(unittest.TestCase):
    def test_extracts_json_from_markdown_fence(self):
        payload = _extract_json('```json\n{"defects": []}\n```')
        self.assertEqual(payload, {"defects": []})

    def test_accepts_list_and_normalizes_severity(self):
        report = _report_from_payload([
            {
                "severity": "критический",
                "title": "Риск",
                "quote": "Цитата",
                "issue": "Проблема",
                "suggestion": "Исправление",
            }
        ])
        self.assertEqual(report.defects[0].severity, "High")

    def test_invalid_enabled_agents_fall_back_to_all(self):
        with patch.dict(os.environ, {"ENABLED_AGENTS": "unknown"}, clear=False):
            settings = get_settings(load_profile(DEFAULT_PROFILE), live=False)
        self.assertEqual(settings.enabled_agents, ["payments", "kyc_aml", "security", "logic"])

    def test_missing_or_broken_defects_are_not_clean_reports(self):
        for invalid in ({}, {"note": []}, {"defects": None}, {"defects": "пусто"}, {"defects": [{}]}):
            with self.subTest(invalid=invalid), self.assertRaises(LLMResponseError):
                _report_from_payload(invalid)
        self.assertEqual(_report_from_payload({"defects": []}).defects, [])

    def test_context_is_data_and_only_owned_criteria_are_in_prompt(self):
        profile = load_profile(DEFAULT_PROFILE)
        spec = "Текст ТЗ и произвольные разделители"
        context = "Дополнительный текст пользователя"
        user = build_user_prompt(spec, context)
        self.assertEqual(json.loads(user.split("\n", 1)[1]), {"specification": spec, "project_context": context})
        system = build_system_prompt(profile, "payments", 8)
        self.assertIn("payment.retry", system)
        self.assertNotIn("data.card", system)
        self.assertNotIn(context, system)
        self.assertIn("на казахском языке", build_system_prompt(profile, "payments", 8, "kk"))

    def test_unexpected_error_does_not_expose_exception_details(self):
        status, message = describe_llm_error(RuntimeError("текст частного документа"))
        self.assertEqual(status, 500)
        self.assertNotIn("частного", message)


if __name__ == "__main__":
    unittest.main()
