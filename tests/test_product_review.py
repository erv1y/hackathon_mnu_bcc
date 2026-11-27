import asyncio
import unittest

from audit_compare import ReportDigest, compare_reports
from audit_documents import DocumentBundle, DocumentSource
from audit_questions import QuestionFinding, QuestionRequest, answer_question
from analyzer import get_settings
from product_review import boundary_inputs, grounded_options, group_findings
from review_insights import build_insights


class ProductReviewTests(unittest.TestCase):
    def setUp(self):
        self.source = "Transfer limit is 100,000 KZT.\nTransfer limit is 500,000 KZT."
        self.bundle = DocumentBundle([DocumentSource(self.source, "rules.md")])
        self.proofs = [
            dict(text=quote, requirement_id=f"R{i}", file_id="D1", start=self.source.index(quote),
                 end=self.source.index(quote) + len(quote))
            for i, quote in enumerate(self.source.splitlines(), 1)
        ]
        self.conflict = dict(id="CON-001", title="Transfer limit", issue="The caps conflict for the same operation.",
                             requirement_ids=["R1", "R2"], severity="Critical", criterion_id="contradiction",
                             suggestion="Approve one cap before implementation.", evidence=self.proofs,
                             action_kind="business_decision", verified="exact")

    def test_grouping_conserves_evidence_and_keeps_separate_risks(self):
        repeat = dict(self.conflict, id="QUAL-003", severity="Medium")
        distinct = dict(repeat, id="QUAL-004", issue="The time window for the cap is missing.")
        groups = group_findings([repeat, self.conflict, distinct])
        self.assertEqual([[row["id"] for row in group] for group in groups],
                         [["CON-001", "QUAL-003"], ["QUAL-004"]])
        self.assertEqual(groups[0][0]["evidence"], self.proofs)

    def test_conflict_questions_and_boundary_tests_remain_conditional_in_all_languages(self):
        for lang in ("ru", "en", "kk"):
            report = build_insights(self.bundle, [self.conflict], get_settings(live=False).profile, "deep", lang)
            question = report["questions"][0]
            self.assertEqual(question["requirement_ids"], ["R1", "R2"])
            self.assertEqual(question["options"], ["100,000", "500,000"])
            self.assertNotEqual(question["question"], question["default"])
            self.assertEqual(report["scenarios"][0]["approval"], "clarification")
            self.assertEqual(report["scenarios"][0]["inputs"], ["99999", "100000", "100001", "499999", "500000", "500001"])
            self.assertEqual(report["scenarios"][0]["evidence"], self.proofs)
        self.assertEqual(grounded_options([{"text": "100 KZT"}, {"text": "500 USD"}]), [])
        self.assertEqual(boundary_inputs(["1.5"]), [])

    def test_local_qa_groups_duplicate_answers_but_keeps_distinct_risks(self):
        def item(id, issue):
            return QuestionFinding(id=id, title="Transfer limit", issue=issue, severity="High",
                                   suggestion="Confirm the applicable rule.",
                                   quotes=self.source.splitlines(), file_ids=["D1", "D1"])
        first = item("F-1", "Transfer caps conflict.")
        duplicate = item("F-2", "Transfer caps conflict.")
        distinct = item("F-3", "Transfer time window is missing.")
        request = QuestionRequest(question="What about the limit?", document=self.source,
                                  previous_question="Transfer cap")
        reply = asyncio.run(answer_question(request, self.source, [first, duplicate, distinct], bundle=self.bundle))
        self.assertEqual([row["id"] for row in reply["items"]], ["F-1", "F-3"])
        self.assertEqual([proof["text"] for proof in reply["items"][0]["evidence"]], self.source.splitlines())

    def test_comparison_distinguishes_modes_and_partial_reports(self):
        baseline = ReportDigest(source_hash=self.bundle.fingerprint, mode="quick", findings=[])
        mode = baseline.model_copy(update={"mode": "deep", "partial": True, "independent_audit": False})
        self.assertEqual(compare_reports(baseline, baseline)["analysis_kind"], "repeat_analysis")
        result = compare_reports(baseline, mode)
        self.assertEqual(result["analysis_kind"], "mode_comparison")
        self.assertTrue(result["coverage_limited"])
        self.assertEqual(result["closed"], [])
