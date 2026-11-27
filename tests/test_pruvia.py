import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from audit_questions import QuestionRequest, answer_question
from document_review import document_review, structure_document
from postprocess import QuoteIndex
from pruvia import main, make_submission_report
from quality_audit import QUALITY_PROMPT, load_quality_rubric, review_quality


SOURCE = (
    "# Проверка переводов\n\n"
    "## Операции\n"
    "* **Ф-1.** Клиент видит квитанцию сразу после перевода.\n"
    "* **Ф-2.** Клиент видит квитанцию только после сверки.\n"
)
QUOTES = ["Клиент видит квитанцию сразу после перевода.", "Клиент видит квитанцию только после сверки."]


def conflict(quotes):
    return {
        "title": "Разные моменты выдачи квитанции",
        "issue": "Команда не может определить момент показа.",
        "suggestion": "Согласовать единое правило выдачи.",
        "severity": "High",
        "quotes": quotes,
    }


class RequirementMapTests(unittest.TestCase):
    def test_markdown_ids_and_unicode_offsets_are_preserved(self):
        source = SOURCE + "\n* **Ф-3.** 🧾 Код карты должен храниться в профиле.\n"
        items = structure_document(source)
        self.assertEqual([item["source_id"] for item in items], ["Ф-1", "Ф-2", "Ф-3"])
        self.assertEqual([item["section"] for item in items], ["Операции"] * 3)
        proof = QuoteIndex(source).match("Код карты должен храниться в профиле.")
        defect = {
            "title": "Хранение кода", "severity": "High", "verified": "exact", "evidence": [proof],
            "suggestion": "Код карты в профиле не хранится.", "fix_kind": "rewrite",
        }
        result = document_review(source, [{"defects": [defect]}], complete=True)
        self.assertEqual(result["requirements"][2]["finding_ids"], ["F-001"])
        self.assertEqual(defect["requirement_ids"], ["REQ-003"])
        fix = defect["fix"]
        self.assertEqual(source[fix["start"]:fix["end"]], fix["original"])
        self.assertEqual(fix["kind"], "replace")

    def test_unstated_timing_and_contradictions_need_a_decision(self):
        source = SOURCE + "\nНовый статус отображается быстро."
        index = QuoteIndex(source)
        first, second = [index.match(quote) for quote in QUOTES]
        vague = index.match("Новый статус отображается быстро.")
        base = {"severity": "High", "verified": "exact", "fix_kind": "rewrite"}
        issues = [
            {**base, "evidence": [first, second], "suggestion": "Показывать после сверки."},
            {**base, "evidence": [vague], "suggestion": "Показывать не позже 2.5 секунды."},
        ]
        result = document_review(source, [{"defects": issues}], complete=False)
        self.assertEqual(issues[0]["requirement_ids"], ["REQ-001", "REQ-002"])
        self.assertEqual([item["fix"]["kind"] for item in issues], ["clarify", "clarify"])
        self.assertEqual([item["priority"] for item in result["requirements"]], ["P0"] * 3)


class OfficialAuditTests(unittest.IsolatedAsyncioTestCase):
    async def test_item_review_and_whole_review_share_source_lines(self):
        calls = []

        async def fake_ask(system, payload):
            self.assertEqual(system, QUALITY_PROMPT)
            calls.append(payload["scope"])
            rows = []
            for item in payload["items"]:
                checks = {key: {"status": "pass", "findings": []} for key in payload["rules"]}
                if item["id"] == "SET":
                    checks["consistency"] = {"status": "fail", "findings": [conflict(QUOTES)]}
                rows.append({"id": item["id"], "checks": checks})
            return {"rows": rows}

        review = await review_quality(SOURCE, load_quality_rubric(), fake_ask)
        markdown, complete = make_submission_report(
            review, document_name="Проверка переводов", file_name="requirements.md",
            model="test-model", agent_version="test-version",
        )
        self.assertEqual(set(calls), {"individual", "whole"})
        self.assertTrue(complete)
        self.assertIn("Версия агента — test-version", markdown)
        self.assertIn("| REQ-001 | Ф-1 | 4–4 |", markdown)
        self.assertIn("### REQ-002", markdown)
        self.assertIn("## Проверка набора целиком", markdown)
        self.assertIn("Критичных нарушений — 1", markdown)
        for row in review["requirements"]:
            self.assertEqual(len(row["checks"]), 5)
        self.assertEqual([proof["line_start"] for proof in review["whole"]["consistency"]["findings"][0]["evidence"]], [4, 5])

    async def test_invalid_evidence_and_missing_rows_make_run_incomplete(self):
        async def fake_ask(_, payload):
            if payload["scope"] == "individual":
                return {"rows": []}
            return {"rows": [{"id": "SET", "checks": {
                "consistency": {"status": "fail", "findings": [conflict([QUOTES[0], QUOTES[0]])]}
            }}]}

        review = await review_quality(SOURCE, load_quality_rubric(), fake_ask)
        markdown, complete = make_submission_report(
            review, document_name="Переводы", file_name="requirements.md", model="test-model", agent_version="test",
        )
        self.assertEqual(review["whole"]["consistency"]["status"], "unknown")
        self.assertEqual(review["requirements"][0]["checks"]["clarity"]["status"], "unknown")
        self.assertFalse(complete)
        self.assertIn("Проверка неполная", markdown)

    async def test_questions_model_cannot_supply_its_own_claim_or_reference(self):
        async def select(_, payload):
            self.assertIn("квитанция", payload["question"])
            return {"ids": ["UNKNOWN", "REQ-001", "REQ-001"], "answer": "Добавленное моделью утверждение"}

        request = QuestionRequest(question="Когда появится квитанция?", document=SOURCE)
        answer = await answer_question(request, SOURCE, [], select)
        self.assertEqual([item["id"] for item in answer["items"]], ["REQ-001"])
        self.assertNotIn("answer", answer)
        self.assertIn(QUOTES[0], answer["items"][0]["evidence"][0]["text"])


class CliTests(unittest.TestCase):
    def test_cli_writes_the_report_to_requested_utf8_file(self):
        async def review(source, settings, rubric):
            self.assertIn("**Ф-1.**", source)
            self.assertEqual(settings.model, "test-model")

            async def fake_ask(_, payload):
                checks = {key: {"status": "pass", "findings": []} for key in payload["rules"]}
                return {"rows": [{"id": item["id"], "checks": checks} for item in payload["items"]]}

            return await review_quality(source, rubric, fake_ask)

        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict(os.environ, {"AUDIT_LOAD_DOTENV": "0"}),
            patch("pruvia.get_settings", return_value=SimpleNamespace(model="test-model")),
            patch("pruvia.run_quality_audit", side_effect=review),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            root = Path(folder)
            source = root / "requirements.md"
            source.write_text(SOURCE, encoding="utf-8")
            out = root / "out" / "review.md"
            self.assertEqual(main([str(source), str(out), "--agent-version", "test"]), 0)
            report = out.read_text(encoding="utf-8")
        self.assertIn("Проверка переводов", report)
        self.assertIn("Модель — test-model", report)
        self.assertIn("Выделенные требования", report)
