import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from analyzer import build_result, get_settings
from audit_profile import DEFAULT_PROFILE, AuditProfile, load_profile
from evaluation import load_replay, load_suite, measure_case, replay_report, summarize, validate_replay, validate_suite


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"ENABLED_AGENTS": "", "MAX_DEFECTS_PER_AGENT": "8"}))
        self.profile = load_profile(DEFAULT_PROFILE)
        self.settings = get_settings(self.profile, live=False)
        self.suite, self.replay = load_suite(), load_replay()

    def test_replay_metrics_distinguish_review_required_from_true_findings(self):
        validate_suite(self.suite, self.settings)
        validate_replay(self.replay, self.suite, self.settings)
        metrics = summarize([
            measure_case(case, replay_report(case, self.replay, self.settings)) for case in self.suite.cases
        ])
        self.assertEqual(metrics["f1"], 0.9231)
        self.assertEqual(metrics["verified_quote_rate"], 0.85)
        self.assertEqual(metrics["high_recall"], 1)

    def test_each_language_has_a_reproducible_before_and_after_report(self):
        for language, sample in self.suite.demo.items():
            pair = {case.id: case for case in self.suite.cases}
            before = replay_report(pair[sample.before], self.replay, self.settings)
            after = replay_report(pair[sample.after], self.replay, self.settings)
            self.assertEqual((before["language"], after["language"]), (language, language))
            self.assertEqual((before["score"], after["score"]), (48, 100))

    def test_profile_drives_weights_and_report_shape(self):
        data = self.profile.model_dump(mode="json")
        data["scoring"]["weights"]["High"] = 20
        data["report"]["fields"] = ["suggestion", "evidence"]
        data["report"]["group_by"] = "agent"
        edited = AuditProfile.model_validate(data)
        settings = get_settings(edited, live=False)
        report = replay_report(self.suite.cases[0], self.replay, settings)
        self.assertEqual(report["score"], 33)
        self.assertEqual(report["pending_findings"], 1)
        self.assertEqual(report["report_format"]["fields"], ["suggestion", "evidence"])
        self.assertEqual(report["report_format"]["group_by"], "agent")
        self.assertNotEqual(edited.fingerprint, self.profile.fingerprint)

    def test_invalid_references_and_invalid_report_format_are_rejected(self):
        data = self.profile.model_dump(mode="json")
        data["criteria"]["data.card"]["references"] = ["unknown"]
        with self.assertRaisesRegex(ValidationError, "unknown"):
            AuditProfile.model_validate(data)
        data = self.profile.model_dump(mode="json")
        data["report"]["fields"] = ["issue", "issue"]
        with self.assertRaisesRegex(ValidationError, "повторов"):
            AuditProfile.model_validate(data)

    def test_disagreement_needs_two_distinct_evidence_fragments(self):
        case = next(case for case in self.suite.cases if case.id == "conflicting-history")
        label = self.replay.observations[case.id]["kyc_aml"][0]
        label.related_quotes = [label.quote]
        report = replay_report(case, self.replay, self.settings)
        module = next(item for item in report["modules"] if item["key"] == "kyc_aml")
        defect = module["defects"][0]
        self.assertEqual(defect["review_reason"], "not_enough_quotes")
        self.assertEqual(defect["score_points"], 0)
        self.assertEqual(measure_case(case, report)["matched"], 0)

    def test_report_is_stable_when_agents_finish_in_a_different_order(self):
        data = self.profile.model_dump(mode="json")
        data["criteria"]["payment.retry"]["agents"] = ["payments", "logic"]
        settings = get_settings(AuditProfile.model_validate(data), live=False)
        case = self.suite.cases[0]
        finding = self.replay.observations[case.id]["payments"][0].model_dump()
        outcomes = {
            key: {"title": agent.title, "ok": True, "error": None, "status_code": 200, "elapsed_sec": 0, "defects": []}
            for key, agent in settings.profile.agents.items()
        }
        outcomes["payments"]["defects"] = [{**finding, "severity": "Low"}]
        outcomes["logic"]["defects"] = [finding]
        first = build_result(outcomes, settings, case.text, 0)
        last = build_result(dict(reversed(list(outcomes.items()))), settings, case.text, 0)
        self.assertEqual(first["modules"], last["modules"])
        self.assertEqual(first["merged_duplicates"], 1)
        self.assertEqual(first["scored_counts"]["High"], 1)
        self.assertEqual(first["modules"][0]["defects"][0]["also_by"], ["Логика и тестируемость"])

    def test_partial_report_keeps_findings_without_readiness_score(self):
        original = self.suite.cases[0]
        report = replay_report(original, self.replay, self.settings)
        by_key = {module["key"]: module for module in report["modules"]}
        by_key["kyc_aml"].update(ok=False, error="Сбой", status_code=504, defects=[])
        partial = build_result(by_key, self.settings, original.text, 0)
        self.assertIsNone(partial["score"])
        self.assertEqual(partial["coverage"]["agents_completed"], 3)
        self.assertEqual(partial["scored_counts"]["High"], 2)
