import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import analyzer
from audit_compare import ReportDigest, compare_reports
from audit_documents import DocumentBundle, DocumentSource, PreparedDocument
from audit_profile import load_profile
from parser import normalize_text
from postprocess import BundleQuoteIndex
from pruvia import main as cli_main
from quality_audit import review_quality
from review_insights import build_insights
from schemas import AuditReport

with patch.dict(os.environ, {"AUDIT_DEMO_ONLY": "1"}):
    import web as main

LIMIT_MAIN = "Daily limit: 100 KZT."
LIMIT_APPENDIX = "Daily limit: 200 KZT."
MAIN = "# Payments\n\n" + LIMIT_MAIN + "\nThe system updates the status quickly.\n"
APPENDIX = "# Operational rules\n\n" + LIMIT_APPENDIX + "\nThe customer receives an operation receipt.\n"
ENV = {"AUDIT_PROFILE": "", "AUDIT_DEMO_DATASET": "", "AUDIT_DEMO_REPLAY": "", "ENABLED_AGENTS": ""}


def make_bundle():
    return DocumentBundle([
        DocumentSource(normalize_text(MAIN), "payments.md"),
        DocumentSource(normalize_text(APPENDIX), "rules.md", "related"),
    ])


class PackageEvidenceTests(unittest.TestCase):
    def test_duplicate_text_needs_document_id_and_keeps_local_coordinates(self):
        quote = "🧾 A customer receives a receipt."
        bundle = DocumentBundle([
            DocumentSource("# One\n" + quote, "one.md"),
            DocumentSource("# Two\n\n" + quote, "two.md", "glossary"),
        ])
        index = BundleQuoteIndex(bundle)
        self.assertIsNone(index.match(quote))
        proof = index.match(quote, "D2")
        self.assertEqual((proof["file_name"], proof["file_line_start"], proof["line_start"]), ("two.md", 3, 6))
        self.assertEqual(bundle.text[proof["start"]:proof["end"]], quote)
        self.assertIsNone(index.match(quote + "\n\n# Two"))

    def test_deep_finds_ambiguous_words_and_cross_document_numbers(self):
        bundle = make_bundle()
        profile = load_profile()
        quick = build_insights(bundle, [], profile, "quick", "en")
        deep = build_insights(bundle, [], profile, "deep", "en")
        self.assertFalse(quick["ambiguities"] or quick["sanity"])
        self.assertEqual([item["title"] for item in deep["ambiguities"]], ["quickly"])
        self.assertEqual([item["criterion_id"] for item in deep["sanity"]], ["feasibility.cross"])
        self.assertEqual([point["file_line_start"] for point in deep["sanity"][0]["evidence"]], [3, 3])
        self.assertEqual({question.get("example", False) for question in deep["questions"]}, {False, True})
        self.assertEqual(set(deep["summaries"]), {"lead", "developer", "tester"})

    def test_impossible_local_time_and_bounds_are_checked_in_quick(self):
        bundle = DocumentBundle([DocumentSource(
            "Допустимый диапазон от 30 до 10 KZT.\n"
            "Подтверждение появляется через -5 минут после операции.",
        )])
        result = build_insights(bundle, [], load_profile(), "quick", "ru")
        self.assertEqual([row["criterion_id"] for row in result["sanity"]], ["feasibility.range", "feasibility.time"])
        self.assertEqual([row["evidence"][0]["file_line_start"] for row in result["sanity"]], [1, 2])

    def test_read_only_and_multiple_methods_do_not_trigger_a_method_alert(self):
        for methods in ("| HTTP method | POST |", "| HTTP method | GET |\n| HTTP method | POST |"):
            bundle = DocumentBundle([DocumentSource("# REST API\n" + methods + "\n### Logic\n1. Update the settings.")])
            insights = build_insights(bundle, [], load_profile(), "quick", "en")
            self.assertNotIn("feasibility.safe_method", [row["criterion_id"] for row in insights["sanity"]])

    def test_threshold_and_calendar_checks_preserve_both_source_lines(self):
        text = (
            "# Учёт покупок\n## Правила\n"
            "Месяц определяется по дате совершения покупки.\n"
            "На карту приходится максимум один такой push за календарный месяц.\n"
            "При изменении порога следующее достижение вызывает push независимо от истории предыдущей настройки.\n"
            "## Приёмка\n"
            "| Покупка 31 января | Провести 1 февраля | Февральский счётчик вырос |"
        )
        bundle = DocumentBundle([DocumentSource(text, "notification.md")])
        rows = build_insights(bundle, [], load_profile(), "deep", "ru")["sanity"]
        self.assertEqual([(row["criterion_id"], [e["file_line_start"] for e in row["evidence"]]) for row in rows], [
            ("feasibility.purchase_month", [3, 7]), ("feasibility.notification_limit", [4, 5]),
        ])

    def test_comparison_distinguishes_fixed_new_and_out_of_scope(self):
        bundle = make_bundle()
        after_sources = [PreparedDocument(text=bundle.sources[0].text.replace("100 KZT", "200 KZT"), name="payments.md")]
        after_hash = DocumentBundle([DocumentSource(after_sources[0].text, "payments.md")]).fingerprint
        source_proof = {"text": LIMIT_MAIN, "file_name": "payments.md"}
        existing = {"id": "F-001", "criterion_id": "client.limits", "title": "Daily limit", "severity": "High", "evidence": [source_proof]}
        before = ReportDigest(source_hash=bundle.fingerprint, findings=[existing])
        new = {**existing, "criterion_id": "payment.status", "title": "Status mismatch"}
        after = ReportDigest(source_hash=after_hash, completed_criteria=["client.limits"], findings=[new])
        fixed = compare_reports(before, after, after_sources)
        self.assertEqual((fixed["closed"], fixed["new"]), (["F-001"], ["F-001"]))
        unknown = compare_reports(before, after.model_copy(update={"completed_criteria": []}), after_sources)
        self.assertEqual(unknown["unassessed"], ["F-001"])
        partial = compare_reports(before, after.model_copy(update={"partial": True}), after_sources)
        self.assertEqual(partial["unassessed"], ["F-001"])
        self.assertEqual(compare_reports(before, after)["unassessed"], ["F-001"])
        same = compare_reports(before, after.model_copy(update={"source_hash": bundle.fingerprint}), after_sources)
        self.assertEqual((same["kind"], same["closed"]), ("same_source", []))


class PackageApiTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, ENV))
        self.http = self.enterContext(TestClient(main.app))

    def test_file_count_and_role_count_are_checked_before_model(self):
        files = [("files", (f"rule-{n}.md", MAIN.encode())) for n in range(6)]
        six = self.http.post("/api/document/inspect", files=files)
        self.assertEqual(six.status_code, 400)
        wrong_roles = self.http.post("/api/document/inspect", files=files[:2], data={"roles": "main"})
        self.assertEqual(wrong_roles.status_code, 400)
        valid = self.http.post("/api/document/inspect", files=files[:5])
        self.assertEqual(valid.status_code, 200, valid.text)
        self.assertEqual(len(valid.json()["files"]), 5)

    def test_local_review_of_imported_refund_example_never_calls_the_model(self):
        example = Path(__file__).resolve().parents[1] / "static" / "examples"
        files = [("files", (path.name, path.read_bytes())) for path in sorted(example.glob("*.md"))]
        payload = {"roles": ["main", "related"], "language": "kk"}
        with patch.object(analyzer, "_ask_llm", new_callable=AsyncMock) as model:
            fast = self.http.post("/api/audit/rules", files=files, data={**payload, "mode": "quick"})
            full = self.http.post("/api/audit/rules", files=files, data={**payload, "mode": "deep"})
            model.assert_not_called()
        self.assertEqual((fast.status_code, full.status_code), (200, 200))
        report = full.json()
        self.assertEqual(report["mode"], "rules")
        self.assertIsNone(report["score"])
        self.assertEqual(fast.json()["counts"]["High"], 1)
        findings = report["modules"][0]["defects"]
        self.assertEqual({item["criterion_id"] for item in findings}, {"feasibility.safe_method", "feasibility.refund_minimum"})
        links = next(item for item in findings if item["criterion_id"] == "feasibility.refund_minimum")
        self.assertEqual([item["file_name"] for item in links["evidence"]], ["01_refund_api.md", "02_refund_policy.md"])
        self.assertTrue(all(report["document"]["text"][e["start"]:e["end"]] == e["text"] for e in links["evidence"]))

class PackageCliTests(unittest.TestCase):
    def test_cli_quick_uses_smaller_rubric_and_reports_files(self):
        async def run(bundle, _, rubric):
            self.assertIsInstance(bundle, DocumentBundle)
            self.assertEqual(set(rubric.individual), {"completeness", "verifiability"})

            async def ask(_, payload):
                self.assertEqual(payload["check_mode"], "quick")
                rows = []
                for item in payload["items"]:
                    checks = {key: {"status": "pass", "findings": []} for key in payload["rules"]}
                    if item["id"] == "SET":
                        self.assertEqual(item["documents"][1]["role"], "appendix")
                        checks["consistency"] = {"status": "fail", "findings": [{
                            "title": "Два лимита", "issue": "Разные значения", "suggestion": "Согласовать лимит",
                            "severity": "High", "quotes": [
                                {"file_id": "D1", "text": LIMIT_MAIN}, {"file_id": "D2", "text": LIMIT_APPENDIX},
                            ],
                        }]}
                    rows.append({"id": item["id"], "checks": checks})
                return {"rows": rows}

            return await review_quality(bundle, rubric, ask)

        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict(os.environ, {**ENV, "AUDIT_LOAD_DOTENV": "0"}),
            patch("pruvia.get_settings", return_value=SimpleNamespace(model="test-model")),
            patch("pruvia.run_quality_audit", side_effect=run),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            root = Path(folder)
            first, second, report = [root / name for name in ("payments.md", "rules.md", "report.md")]
            first.write_text(MAIN, encoding="utf-8")
            second.write_text(APPENDIX, encoding="utf-8")
            self.assertEqual(cli_main([str(first), str(report), "--mode", "quick", "--file", f"appendix={second}"]), 0)
            output = report.read_text(encoding="utf-8")
        self.assertIn("Режим — быстрый", output)
        self.assertIn(r"rules\.md · строка 3", output)
        self.assertIn("Вопросы заказчику", output)
        self.assertIn("Сводка по ролям", output)
