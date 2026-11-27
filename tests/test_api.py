import asyncio
import io
import json
import os
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from docx import Document
from fastapi.testclient import TestClient

import analyzer
from audit_profile import DEFAULT_PROFILE, load_profile
from evaluation import load_replay, load_suite
from schemas import AuditReport
from tests.test_parser import make_pdf

# Запуск тестов всегда отключает чтение .env до импорта приложения.
with patch.dict(os.environ, {"AUDIT_DEMO_ONLY": "1"}):
    import web as main

ENV = {"AUDIT_PROFILE": "", "ENABLED_AGENTS": "", "AUDIT_DEMO_DATASET": "", "AUDIT_DEMO_REPLAY": ""}


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, ENV))
        self.http = self.enterContext(TestClient(main.app))
        self.case = load_suite().cases[0]

    def test_health_and_demo_localize_all_three_languages(self):
        self.assertEqual(self.http.get("/api/health").json(), {"status": "ok"})
        for locale, title in (("ru", "Извлечение требований"), ("kk", "Талаптарды шығару"), ("en", "Extractor")):
            with self.subTest(locale=locale):
                config = self.http.get("/api/config", params={"language": locale}).json()
                self.assertEqual(config["agents"][0]["title"], title)
                self.assertFalse(config["api_key_configured"])
                self.assertTrue(config["demo_available"])
                response = self.http.get("/api/demo", params={"language": locale})
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                report = response.json()["before"]["report"]
                self.assertEqual((report["mode"], report["language"], report["score"]), ("replay", locale, 48))

    def test_questions_about_demo_return_two_verified_source_lines(self):
        response = self.http.post("/api/audit/ask", json={"demo_version": "before", "intent": "clarify"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        contradiction = next(item for item in response.json()["items"] if len(item["evidence"]) == 2)
        source = self.case.text.splitlines()
        for proof in contradiction["evidence"]:
            self.assertIn(proof["text"], source[proof["line_start"] - 1])
        self.assertIn("Чек выдаём", contradiction["clarification"])

    def test_questions_drop_client_supplied_findings_with_unverified_quotes(self):
        response = self.http.post("/api/audit/ask", json={
            "intent": "risks", "document": self.case.text,
            "findings": [{
                "id": "F-002", "title": "Проверка", "issue": "Причина", "suggestion": "Исправление",
                "severity": "High", "quotes": ["В исходном тексте такой фразы нет."],
            }],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], [])

    def test_home_audit_and_guide_are_distinct_pages(self):
        home = self.http.get("/")
        workspace = self.http.get("/audit")
        demo_page = self.http.get("/audit", params={"demo": 1})
        guide = self.http.get("/how")
        self.assertEqual((home.status_code, workspace.status_code, demo_page.status_code, guide.status_code), (200, 200, 200, 200))
        self.assertIn('data-page="home"', home.text)
        self.assertIn('href="/audit"', home.text)
        self.assertIn('href="/audit?demo=1"', home.text)
        self.assertNotIn('id="spec-text"', home.text)
        self.assertIn('data-page="audit"', workspace.text)
        self.assertIn('id="spec-text"', workspace.text)
        self.assertIn('id="results"', workspace.text)
        self.assertNotIn('class="hero"', workspace.text)
        self.assertEqual(workspace.text, demo_page.text)
        self.assertEqual(home.headers["Content-Security-Policy"], workspace.headers["Content-Security-Policy"])
        self.assertEqual(home.headers["Content-Security-Policy"], guide.headers["Content-Security-Policy"])
        self.assertIn('href="/how" data-nav="how"', home.text)
        self.assertNotIn('id="agents"', home.text)
        self.assertIn('data-page="how"', guide.text)
        self.assertIn('id="agents"', guide.text)
        self.assertIn('href="/audit?example=refund"', guide.text)

    def test_inspect_document_describes_file_without_a_model(self):
        source = "# Проверка\n\n## Платежи\nЗапрос на повтор не создаёт второго списания.\n"
        response = self.http.post("/api/document/inspect", files={"file": ("requirements.md", source.encode("utf-8"))})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "format": "MD", "requirements": 1, "section_count": 1, "sections": ["Платежи"], "warnings": [],
        })

    def test_live_requires_consent_and_a_configured_key(self):
        response = self.http.post("/api/audit", data={"text": self.case.text})
        self.assertEqual(response.status_code, 403)
        response = self.http.post("/api/audit", data={"text": self.case.text, "consent": True})
        self.assertEqual(response.status_code, 503)

    def test_guard_rejects_unauthorized_and_oversize_requests_early(self):
        with patch.object(main, "DEMO_PASSWORD", "test-access-only"):
            denied = self.http.post("/api/audit", content=b"not a form")
        self.assertEqual(denied.status_code, 401)
        with patch.object(main, "MAX_BODY_BYTES", 64):
            rejected = self.http.post("/api/audit", data={"text": "a" * 80})
            chunked = self.http.post(
                "/api/audit",
                content=iter([b"text=", b"a" * 40, b"a" * 40]),
                headers={"Content-Type": "application/x-www-form-urlencoded", "Accept-Language": "en"},
            )
        self.assertEqual(rejected.status_code, 413)
        self.assertEqual(chunked.status_code, 413, chunked.text)
        self.assertIn("too large", chunked.json()["detail"])


class CancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_stopping_stream_cancels_agent_and_closes_client(self):
        settings = analyzer.get_settings(load_profile(DEFAULT_PROFILE), live=False)
        settings.enabled_agents = ["payments"]
        began = asyncio.Event()
        stopped = asyncio.Event()
        fake = SimpleNamespace(close=AsyncMock())

        async def complete(*_):
            began.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        with patch.object(analyzer, "_get_client", return_value=fake), patch.object(analyzer, "_ask_llm", side_effect=complete):
            events = analyzer.audit_events("Достаточно длинный текст технического задания", settings=settings)
            self.assertEqual((await anext(events))["type"], "start")
            pending = asyncio.create_task(anext(events))
            await asyncio.wait_for(began.wait(), timeout=2)
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
            self.assertTrue(stopped.is_set())
            fake.close.assert_awaited_once()
