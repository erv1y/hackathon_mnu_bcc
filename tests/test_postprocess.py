import unittest

from postprocess import QuoteIndex, refine, verify_quote


def outcome(title, severity, defect_title, quote, issue="Риск повторного списания"):
    return {
        "title": title,
        "ok": True,
        "defects": [
            {
                "severity": severity,
                "title": defect_title,
                "quote": quote,
                "issue": issue,
                "suggestion": "Использовать ключ идемпотентности.",
            }
        ],
    }


class QuoteVerificationTests(unittest.TestCase):
    def test_exact_quote_ignores_typographic_quotes_and_whitespace(self):
        spec = "Система создаёт платёж  «только один раз»."
        self.assertEqual(verify_quote(spec, 'Система создаёт платёж "только один раз".'), "exact")

    def test_missing_requirement_marker(self):
        self.assertEqual(verify_quote("Текст", "Отсутствует в тексте ТЗ"), "missing")

    def test_invented_quote_is_unverified(self):
        self.assertEqual(verify_quote("В ТЗ этого нет", "Система обязана хранить CVV"), "unverified")

    def test_quote_with_matching_prefix_and_invented_tail_is_rejected(self):
        spec = "При успешной оплате заказ получает статус «Оплачен», при отказе — «Не оплачен»."
        quote = "При успешной оплате заказ получает статус «Оплачен», а при отказе заказ всё равно помечается оплаченным."
        self.assertEqual(verify_quote(spec, quote), "unverified")
        self.assertEqual(verify_quote(spec, ""), "unverified")

    def test_evidence_points_to_the_original_extracted_lines(self):
        spec = "Статусы\nЗаявка находится в  «обработке»\nдо ответа партнёра."
        result = QuoteIndex(spec).match('Заявка находится в "обработке" до ответа партнёра.')
        self.assertEqual((result["line_start"], result["line_end"]), (2, 3))
        self.assertEqual(spec[result["start"]:result["end"]], result["text"])


class DeduplicationTests(unittest.TestCase):
    def test_duplicate_keeps_highest_severity_independent_of_agent_order(self):
        spec = "Повторный запрос может создать второй платёж."
        outcomes = {
            "logic": outcome("Логика", "Low", "Повторный платёж", spec),
            "payments": outcome("Платежи", "High", "Повторный платёж", spec),
        }

        merged = refine(outcomes, spec)

        self.assertEqual(merged, 1)
        self.assertEqual(outcomes["logic"]["defects"][0]["severity"], "High")
        self.assertEqual(outcomes["logic"]["defects"][0]["also_by"], ["Платежи"])
        self.assertEqual(outcomes["payments"]["defects"], [])

    def test_distinct_findings_are_not_merged(self):
        outcomes = {
            "payments": outcome("Платежи", "High", "Нет идемпотентности", "Отсутствует в тексте ТЗ"),
            "security": outcome("ИБ", "High", "CVV хранится открыто", "Сохранять CVV в базе"),
        }
        self.assertEqual(refine(outcomes, "Сохранять CVV в базе"), 0)

    def test_same_title_at_two_source_locations_is_not_a_duplicate(self):
        outcomes = {
            "first": outcome("Один", "High", "Не задан срок", "Возврат выполнится в разумный срок."),
            "second": outcome("Два", "High", "Не задан срок", "Квитанция появится достаточно быстро."),
        }
        spec = "Возврат выполнится в разумный срок.\nКвитанция появится достаточно быстро."
        self.assertEqual(refine(outcomes, spec), 0)


if __name__ == "__main__":
    unittest.main()
