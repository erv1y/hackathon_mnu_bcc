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

    def test_verified_vagueness_can_require_business_authority(self):
        _, _, quality, consistency, result = fixtures()
        row = result.decisions[1]
        row.title = "Требование о быстром завершении непроверяемо"
        row.action_kind = "business_decision"
        normalize_audit(quality, consistency, result)
        self.assertEqual(row.assessment_kind, "defect")
        self.assertEqual(row.action_kind, "business_decision")
        self.assertEqual(row.final_severity, "high")

    def test_references_require_evidence_and_duplicate_quotes_are_removed(self):
        extraction, _, quality, consistency, result = fixtures()
        findings = auditor.prepare_findings(quality, consistency)
        row = result.decisions[1]
        row.final_evidence.append(row.final_evidence[0].model_copy())
        auditor.validate_audit(result, findings, extraction)
        self.assertEqual(len(row.final_evidence), 2)
        row.explanation = "Срок из R3 тоже затронут."
        with self.assertRaisesRegex(auditor.AuditValidationError, "unsupported_explanation_reference"):
            auditor.validate_audit(result, findings, extraction)

    def test_manual_candidate_can_merge_with_an_equivalent_candidate(self):
        extraction, _, quality, consistency, result = fixtures()
        target = result.decisions[1]
        target.assessment_kind = "potential_risk"
        review = target.model_copy(deep=True, update={"finding_id": "REVIEW-870"})
        result.decisions.append(review)
        findings = auditor.prepare_findings(quality, consistency)
        findings.append({**findings[1], "id": review.finding_id})
        proposal = {"merges": [{"finding_id": review.finding_id,
                                "merged_into": target.finding_id,
                                "audit_reason": "Identical access scenario and required decision"}]}
        with patch.object(auditor, "ask_gemini", return_value=json.dumps(proposal)):
            audited = auditor.review_global_groups(result, findings, extraction, {review.finding_id})
        self.assertEqual(audited.decisions[-1].merged_into, target.finding_id)
        self.assertEqual(audited.decisions[2].decision, "revised")

    def test_equal_anchors_allow_cross_category_security_review_and_retry(self):
        extraction, _, quality, consistency, result = fixtures()
        source, target, _ = result.decisions
        source.decision, source.final_severity, source.merged_into = "confirmed", "critical", None
        target.requirement_ids = source.requirement_ids[:]
        target.final_evidence = source.final_evidence[:]
        findings = auditor.prepare_findings(quality, consistency)
        findings[0]["criterion"] = "correctness"
        findings[1].update(category="missing_requirement", requirement_ids=source.requirement_ids[:])
        proposal = {"merges": [{"finding_id": source.finding_id, "merged_into": target.finding_id,
                                "audit_reason": "Same disclosure and preconditions."}]}
        with patch.object(auditor, "ask_gemini", side_effect=["{bad", json.dumps(proposal)]) as model:
            audited = auditor.review_global_groups(result, findings, extraction)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(audited.decisions[0].merged_into, target.finding_id)
        self.assertEqual(audited.decisions[1].final_severity, "critical")

    def test_evidence_prefix_normalization_retains_distinct_passages(self):
        self.assertEqual(auditor.passage_key("- Verify the recipient."), "Verify the recipient.")
        self.assertEqual(auditor.passage_key("2. Verify the recipient."), "Verify the recipient.")
        self.assertNotEqual(auditor.passage_key("Verify the recipient."),
                            auditor.passage_key("Verify the sender."))
