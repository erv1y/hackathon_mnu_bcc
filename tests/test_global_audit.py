"""Independent synthetic audit groups with competing settlement fees."""
import json
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from agents import auditor
from core.audit_metrics import audit_metrics
from orchestrator import normalize_audit
from test_auditor_v08 import fixtures


class GlobalAuditTests(unittest.TestCase):
    def test_fee_group_preserves_both_policies_and_distinct_timing_defect(self):
        extraction, _, quality, consistency, result = fixtures()
        member, target, timing = result.decisions
        texts = ["Settlement costs 2 EUR.", "Settlement costs 4 EUR.", "Settlement takes one day."]
        for req, text in zip(extraction.requirements, texts):
            req.text = text
        for row in result.decisions:
            for proof in row.final_evidence:
                proof.quote = texts[int(proof.requirement_id[1:]) - 1]
        quality_finding = quality.assessments[0].findings[0]
        quality_finding.evidence = texts[0]
        quality_finding.explanation = "The quoted price disagrees with the tariff."
        for finding, row in zip(consistency.findings, (target, timing)):
            finding.evidence = list(row.final_evidence)
        target.requirement_ids.reverse()
        member.final_evidence[0].quote = "2 EUR"
        member.decision, member.final_severity, member.merged_into = "confirmed", "high", None
        member.root_cause = "Fee undefined for one tariff."
        target.root_cause = "Mutually exclusive prices."
        timing.root_cause = "Settlement deadline has no starting event."
        findings = auditor.prepare_findings(quality, consistency)
        proposal = {"merges": [dict(finding_id=member.finding_id, merged_into=target.finding_id,
                                    audit_reason="Same settlement fee decision")]}
        with patch.object(auditor, "ask_gemini", return_value=json.dumps(proposal)):
            got = auditor.review_global_groups(result, findings, extraction)
        auditor.validate_audit(got, findings, extraction)
        normalized = normalize_audit(quality, consistency, got)
        merged, representative, distinct = normalized.decisions
        self.assertEqual(merged.merged_into, representative.finding_id)
        self.assertEqual({e.quote for e in representative.final_evidence}, {texts[0], texts[1], "2 EUR"})
        self.assertEqual(distinct.final_severity, "medium")
        self.assertEqual(representative.action_kind, "business_decision")
        self.assertNotRegex(representative.recommendation, "2 EUR|4 EUR|REWRITE")
        metrics = audit_metrics(SimpleNamespace(audit=got, quality=quality, passport={}))
        self.assertEqual((metrics["counts"]["High"], metrics["unique_findings"], metrics["merged_duplicates"]),
                         (1, 2, 1))
        self.assertEqual(metrics["score"], 95)

    def test_open_question_is_not_counted_as_a_confirmed_defect(self):
        _, _, quality, _, result = fixtures()
        result.decisions[2].assessment_kind = "question"
        metrics = audit_metrics(SimpleNamespace(audit=result, quality=quality, passport={}))
        self.assertEqual(metrics["unique_findings"], 1)
        self.assertEqual(metrics["human_review"], 1)
        self.assertEqual(metrics["counts"]["Medium"], 0)

    def test_single_sided_conflict_fails_complete_validation(self):
        extraction, _, quality, consistency, result = fixtures()
        target = result.decisions[1]
        target.requirement_ids = target.requirement_ids[:1]
        target.final_evidence = target.final_evidence[:1]
        with self.assertRaises(auditor.AuditValidationError) as failure:
            auditor.validate_audit(result, auditor.prepare_findings(quality, consistency), extraction)
        self.assertIn("missing_contradiction_side", str(failure.exception))
