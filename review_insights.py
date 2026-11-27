"""Дополнительные проверки по исходному тексту и подтверждённым замечаниям."""

import re
from collections import defaultdict
from product_review import boundary_inputs, grounded_options, group_findings
from decimal import Decimal

from audit_documents import DocumentBundle
from audit_profile import AuditProfile
from contract_checks import DEEP_RULES, GENERAL_RULES, detect_contract_checks
from languages import Language
from postprocess import SEV_RANK
from schemas import CheckMode

_NUMBER = r"[-−]?(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+)(?:[.,]\d+)?"
_RANGE = re.compile(
    rf"\b(?:диапазон|интервал|range|аралық|ауқым)\b[^.!?\n]{{0,45}}?:?\s*(?:от|from)\s+({_NUMBER})\s+(?:до|to)\s+({_NUMBER})",
    re.IGNORECASE,
)
_NEGATIVE_TIME = re.compile(
    rf"\b(?:через|за|within|after)\s+([-−]\d+(?:[.,]\d+)?)\s*(?:мс|сек\w*|мин\w*|час\w*|дн\w*|ms|seconds?|minutes?|hours?|days?|секунд|минут|сағат|күн)\b",
    re.IGNORECASE,
)
_VALUE_LINE = re.compile(
    rf"^\s*(?:[-*]\s+)?(?P<name>[^:;=]{{3,75}}?)\s*[:=]\s*(?P<value>{_NUMBER})\s*(?P<unit>[\w₸$%]+)\s*\.?\s*$",
    re.IGNORECASE,
)
_VALUE_KEY = re.compile(r"\b(?:лимит|лимиты|срок|тайм-аут|limit|timeout|deadline|шек|мерзім)\b", re.IGNORECASE)

_COPY = {
    "ru": {
        "range": ("Перепутаны границы диапазона", "Какие нижняя и верхняя границы верны?", "Зафиксировать диапазон, где нижняя граница не превышает верхнюю."),
        "time": ("Указан отрицательный срок", "От какого события и сколько времени отсчитываем?", "Отсчитывать неотрицательное время от согласованного события."),
        "cross": ("Разные значения параметра в файлах", "Какое значение действует для одного сценария?", "Согласовать один источник значения и использовать его во всех связанных документах."),
        "question": "Какое правило должно действовать в этом случае? {title}",
        "lead": "Критичных замечаний: {high}. Вопросов для согласования: {questions}.",
        "developer": "Замечаний с предложенным решением: {count}. Начните с критичных пунктов ниже.",
        "tester": "Защитных сценариев: {scenarios}. Проверяйте наблюдаемый результат каждого.",
    },
    "kk": {
        "range": ("Аралық шектері ауысып кеткен", "Төменгі және жоғарғы шектің дұрыс мәндері қандай?", "Төменгі шегі жоғарғы шектен аспайтын аралықты бекіту."),
        "time": ("Теріс мерзім көрсетілген", "Уақыт қай оқиғадан басталады және ұзақтығы қандай?", "Уақытты келісілген оқиғадан бастап, оң немесе нөлдік мәнмен белгілеу."),
        "cross": ("Файлдардағы параметр мәндері әртүрлі", "Бір сценарий үшін қандай мән қолданылады?", "Мәннің бір дереккөзін келісіп, байланысты құжаттарда қолдану."),
        "question": "Бұл жағдайда қандай ереже қолданылады? {title}",
        "lead": "Маңызды ескертулер: {high}. Келісілетін сұрақтар: {questions}.",
        "developer": "Шешімі ұсынылған ескертулер: {count}. Алдымен төмендегі маңызды тармақтарды түзетіңіз.",
        "tester": "Қорғаныс сценарийлері: {scenarios}. Әрқайсының бақыланатын нәтижесін тексеріңіз.",
    },
    "en": {
        "range": ("The range bounds are reversed", "What are the correct lower and upper bounds?", "Specify a range whose lower bound is no greater than its upper bound."),
        "time": ("A negative elapsed time is specified", "What event starts the clock, and how long should it run?", "Measure a non-negative duration from the agreed event."),
        "cross": ("Files specify different parameter values", "Which value applies to the same scenario?", "Agree on one source of truth and use it across the related documents."),
        "question": "Which rule should apply in this case? {title}",
        "lead": "Critical findings: {high}. Questions to resolve: {questions}.",
        "developer": "Findings with a proposed change: {count}. Start with the critical items below.",
        "tester": "Defensive checks: {scenarios}. Verify the observable result of each.",
    },
}

_CONTRACT_COPY = {
    "safe_method": {
        "ru": ("GET и изменение данных в одном разделе", "Одна ли операция описана в обеих строках?", "Разделить чтение и запись. Для записи согласовать метод и ожидаемое изменение данных."),
        "kk": ("Бір бөлімде GET және деректерді өзгерту көрсетілген", "Екі жолда бір операция сипатталған ба?", "Оқу мен жазуды ажыратыңыз. Жазу әдісін және деректердің күтілетін өзгерісін келісіңіз."),
        "en": ("GET and a data change appear in the same section", "Do both lines describe the same operation?", "Separate reading from writing. Agree on the write method and the expected data change."),
    },
    "refund_minimum": {
        "ru": ("Разный минимум суммы возврата", "Какой минимум действует для одной операции в обоих файлах?", "Согласовать минимальную сумму и закрепить её в правилах, описании API и тестах."),
        "kk": ("Қайтарудың ең төменгі сомалары әртүрлі", "Екі файлдағы бір операция үшін қай шек қолданылады?", "Ең төменгі соманы келісіп, ережелерде, API сипаттамасында және тестілерде бекітіңіз."),
        "en": ("The minimum refund amount differs", "Which minimum applies to the same operation in both files?", "Agree on a minimum and use it in the rules, API contract and tests."),
    },
    "disjoint_intervals": {
        "ru": ("Допустимые диапазоны не пересекаются", "Действуют ли границы при одинаковых условиях?", "Указать разные условия применения или согласовать один диапазон."),
        "kk": ("Рұқсат етілген аралықтар қиылыспайды", "Бұл шектер бірдей шарттарда қолданыла ма?", "Қолдану шарттарын бөлек жазыңыз немесе бір аралықты келісіңіз."),
        "en": ("The allowed ranges do not overlap", "Do both ranges apply under the same conditions?", "Specify separate conditions or agree on one range."),
    },
    "purchase_month": {
        "ru": ("Правило и тест выбирают разные месяцы", "Какую дату использовать для учёта покупки на границе месяца?", "Согласовать дату учёта и синхронизировать с ней ожидаемый результат теста."),
        "kk": ("Ереже мен тест әртүрлі айды таңдайды", "Ай ауысқанда сатып алуды қай күнмен есептейміз?", "Есепке алу күнін келісіп, тестінің күтілетін нәтижесін соған сәйкестендіріңіз."),
        "en": ("The rule and test select different months", "Which date determines the purchase month at a month boundary?", "Agree on the accounting date and align the expected test result."),
    },
    "notification_limit": {
        "ru": ("Неясно, действует ли лимит после изменения порога", "Сохраняется ли месячный лимит уведомлений при изменении порога?", "Зафиксировать приоритет правил и добавить критерий приёмки для смены порога."),
        "kk": ("Шек өзгергеннен кейін хабарлама лимиті түсініксіз", "Шек өзгергенде айлық хабарлама лимиті сақтала ма?", "Ережелердің басымдығын бекітіп, шек өзгеретін жағдайға қабылдау өлшемін қосыңыз."),
        "en": ("The notification limit after a threshold change is unclear", "Does the monthly notification limit still apply after the threshold changes?", "Specify which rule takes priority and add an acceptance criterion for the change."),
    },
}

_SCENARIO_COPY = {
    "retry": {
        "ru": ("У операции уже есть подтверждённый результат.", "Сверить результат повторной обработки с исходным.", "Денежный эффект возникает один раз."),
        "kk": ("Операцияның расталған нәтижесі бар.", "Қайта өңдеу нәтижесін бастапқы нәтижемен салыстыру.", "Ақшаға әсері бір рет қана болады."),
        "en": ("The operation already has a confirmed result.", "Check that reprocessing preserves the original outcome.", "The financial effect occurs once."),
    },
    "limit": {
        "ru": ("Значение находится на границе или выше согласованного лимита.", "Сверить поведение с включённостью границы в ТЗ.", "Запрещённая операция отклоняется, баланс и остаток лимита сохраняются."),
        "kk": ("Мән келісілген лимиттің шегінде немесе одан жоғары.", "Рұқсатты тапсырмадағы шек ережесімен салыстыру.", "Рұқсатсыз операция қабылданбайды, баланс пен лимит қалдығы сақталады."),
        "en": ("The amount is at or above the agreed limit.", "Check the result against the inclusive or exclusive boundary.", "A prohibited operation is rejected and balances remain correct."),
    },
    "access": {
        "ru": ("У роли нет разрешения на описанное действие или данные.", "Проверить решение о доступе и итоговое состояние.", "Система защищает данные и состояние операции."),
        "kk": ("Рөлде әрекетке немесе деректерге қол жеткізу құқығы жоқ.", "Рұқсат шешімін және соңғы күйді тексеру.", "Жүйе деректер мен операция күйін қорғайды."),
        "en": ("The role has no permission for the action or data.", "Check the access decision and resulting state.", "The system protects the data and the operation state."),
    },
    "status": {
        "ru": ("Текущий статус операции известен.", "Сверить переход с разрешёнными в ТЗ.", "Недопустимый переход отклоняется, итоговое состояние остаётся согласованным."),
        "kk": ("Операцияның ағымдағы күйі белгілі.", "Күй ауысуын рұқсат етілген ережелермен салыстыру.", "Рұқсатсыз ауысу қабылданбайды, соңғы күй үйлесімді болады."),
        "en": ("The current operation state is known.", "Check the transition against the permitted states.", "An invalid transition is rejected and the final state stays consistent."),
    },
    "conflict": {
        "ru": ("Два правила относятся к одному условию.", "Согласовать применимое правило и проверяемый результат.", "В связанных документах и сценариях действует единое правило."),
        "kk": ("Екі ереже бір шартқа қатысты.", "Қолданылатын ереже мен тексерілетін нәтижені келісу.", "Байланысты құжаттар мен сценарийлерде бір ереже қолданылады."),
        "en": ("Two rules apply to the same condition.", "Agree on the applicable rule and observable outcome.", "Related documents and scenarios use the same rule."),
    },
    "test": {
        "ru": ("Наступает описанное условие или пограничный случай.", "Проверить наблюдаемый результат по согласованному критерию.", "Результат можно однозначно подтвердить."),
        "kk": ("Сипатталған шарт немесе шектік жағдай орын алды.", "Келісілген өлшем бойынша бақыланатын нәтижені тексеру.", "Нәтижені бірмәнді растауға болады."),
        "en": ("The documented condition or boundary case is reached.", "Check the observable result using the agreed acceptance criterion.", "The result can be confirmed unambiguously."),
    },
}
_CASE_BY_RULE = {
    "payment.retry": "retry", "payment.refund": "retry", "client.limits": "limit",
    "data.access": "access", "data.privacy": "access", "data.card": "access",
    "client.identity": "access", "payment.status": "status", "data.messages": "status",
    "spec.contradiction": "conflict", "spec.dependencies": "conflict", "spec.sanity": "conflict",
    "consistency": "conflict", "safe_business": "status", "feasibility": "conflict",
    "feasibility.refund_minimum": "conflict", "feasibility.safe_method": "conflict",
    "feasibility.disjoint_intervals": "conflict", "feasibility.purchase_month": "conflict",
    "feasibility.notification_limit": "conflict",
}


def _decimal(value: str) -> Decimal:
    return Decimal(re.sub(r"\s+", "", value).replace("−", "-").replace(",", "."))


def ambiguity_findings(bundle: DocumentBundle, profile: AuditProfile, language: Language) -> list[dict]:
    found = []
    for rule_id, translations in profile.ambiguity_dictionary.items():
        copy = translations.get(language) or next(iter(translations.values()))
        terms = {term for variant in translations.values() for term in variant.terms}
        parts = [r"\s+".join(re.escape(part) for part in term.split()) for term in sorted(terms, key=lambda word: (-len(word), word))]
        phrase = re.compile(r"(?<!\w)(?:" + "|".join(parts) + r")(?!\w)", re.IGNORECASE)
        for position, source in enumerate(bundle.sources):
            for line in re.finditer(r"[^\n]+", source.text):
                match = phrase.search(line.group())
                if match:
                    proof = bundle.excerpt(position, line.start(), line.end())
                    found.append({
                        "id": f"A-{len(found) + 1:03d}", "criterion_id": f"ambiguity.{rule_id}", "severity": "Medium",
                        "title": match.group(), "evidence": [proof], "question": copy.question, "default": copy.example,
                    })
                    if len(found) == 40:
                        return found
    return found


def feasibility_findings(bundle: DocumentBundle, language: Language, *, cross_file: bool) -> list[dict]:
    copy = _COPY[language]
    numbers = defaultdict(dict)
    found = []

    def add(kind: str, evidence: list[dict]) -> None:
        title, question, option = _CONTRACT_COPY[kind][language] if kind in _CONTRACT_COPY else copy[kind]
        found.append({
            "id": f"S-{len(found) + 1:03d}", "criterion_id": f"feasibility.{kind}",
            "severity": "Medium" if kind == "notification_limit" else "High",
            "title": title, "evidence": evidence, "question": question, "default": option,
        })

    for position, source in enumerate(bundle.sources):
        for line in re.finditer(r"[^\n]+", source.text):
            content = line.group()
            proof = None
            bounds = _RANGE.search(content)
            if bounds and _decimal(bounds[1]) > _decimal(bounds[2]):
                proof = bundle.excerpt(position, line.start(), line.end())
                add("range", [proof])
            if _NEGATIVE_TIME.search(content):
                proof = proof or bundle.excerpt(position, line.start(), line.end())
                add("time", [proof])
            value = _VALUE_LINE.fullmatch(content) if cross_file else None
            if value and _VALUE_KEY.search(value["name"]):
                key = (" ".join(value["name"].casefold().split()), value["unit"].casefold())
                values_in_file = numbers[key].setdefault(position, {})
                values_in_file[_decimal(value["value"])] = bundle.excerpt(position, line.start(), line.end())
            if len(found) >= 40:
                return found[:40]

    for values_by_file in numbers.values():
        # Не сравниваем параметр, описанный несколькими значениями в одном файле.
        if len(values_by_file) < 2 or any(len(values) != 1 for values in values_by_file.values()):
            continue
        distinct = {value for values in values_by_file.values() for value in values}
        if len(distinct) > 1:
            first, *others = [next(iter(values.items())) for values in values_by_file.values()]
            other = next(proof for value, proof in others if value != first[0])
            add("cross", [first[1], other])
            if len(found) == 40:
                break
    for rule, proof in detect_contract_checks(bundle, deep=cross_file):
        if len(found) == 40:
            break
        add(rule, proof)
    return found


def insight_criteria(profile: AuditProfile, mode: CheckMode) -> list[str]:
    plan = profile.check_plan(mode)
    checks = [f"ambiguity.{key}" for key in profile.ambiguity_dictionary] if plan.ambiguities else []
    if plan.feasibility:
        rules = ("range", "time", *GENERAL_RULES)
        if mode == "deep":
            rules += ("cross", *DEEP_RULES)
        checks.extend(f"feasibility.{rule}" for rule in rules)
    return checks


def build_insights(bundle: DocumentBundle, findings: list[dict], profile: AuditProfile,
                   mode: CheckMode, language: Language, *, requirements=()) -> dict:
    plan = profile.check_plan(mode)
    copy = _COPY[language]
    ambiguities = ambiguity_findings(bundle, profile, language) if plan.ambiguities else []
    sanity = feasibility_findings(bundle, language, cross_file=mode == "deep") if plan.feasibility else []
    verified = sorted(
        (group[0] for group in group_findings(findings) if not group[0].get("review_reason")),
        key=lambda item: (SEV_RANK[item["severity"]], item["id"]),
    )
    questions = [
        {
            "finding_id": item["id"], "severity": item["severity"], "title": item["title"],
            "question": copy["question"].format(title=item["title"]),
            "reason": item["issue"], "default": item["suggestion"], "evidence": item["evidence"],
            "requirement_ids": item.get("requirement_ids", []), "role": "processOwner",
            "options": grounded_options(item["evidence"]) if item.get("action_kind") == "business_decision" else [],
        }
        for item in verified if item.get("fix", {}).get("kind", "clarify") in {"clarify", "business_decision"}
    ]
    questions.extend({**row, "finding_id": None, "example": row["id"].startswith("A-")} for row in [*sanity, *ambiguities])
    questions.sort(key=lambda item: SEV_RANK[item["severity"]])
    for pos, question in enumerate(questions, start=1):
        question["id"] = f"Q-{pos:03d}"

    scenarios = []
    for item in [*verified, *sanity]:
        if not item["evidence"]:
            continue
        kind = _CASE_BY_RULE.get(item["criterion_id"], "test")
        condition, check, expected = _SCENARIO_COPY[kind][language]
        evidence = item["evidence"]
        alternatives = grounded_options(evidence)
        conditional = bool(alternatives or item.get("action_kind") == "business_decision"
                           or item["criterion_id"] in {
                               "correctness", "contradiction", "spec.contradiction", "inconsistent_constraint", "scope_conflict",
                               "missing_requirement", "missing_dependency", "unambiguity", "clarity",
                               "completeness", "verifiability", "feasibility",
                           })
        inputs = boundary_inputs(alternatives)
        if item.get("requirement_ids"):
            quote_text = " ".join(proof["text"] for proof in evidence)
            if re.search(r"\b(?:unauthori[sz]ed|authentication|access|доступ\w*|авторизац\w*|рұқсат\w*)\b",
                         quote_text, re.IGNORECASE):
                kind = "security"
            elif re.search(r"\b(?:status|state|статус\w*|состояни\w*|мәртебе\w*)\b", quote_text, re.IGNORECASE):
                kind = "transition"
            elif conditional and not inputs:
                kind = "negative"
            condition = "\n".join(proof["text"] for proof in evidence)
            check = {
                "en": "Exercise the scenario described in the cited requirement and record the observed outcome.",
                "ru": "Выполните сценарий из процитированного требования и зафиксируйте фактический результат.",
                "kk": "Дәйексөздегі талаптың сценарийін орындап, нақты нәтижені тіркеңіз.",
            }[language]
            if kind == "security":
                check = {
                    "en": "Exercise the cited operation with the permitted and restricted roles described in the evidence.",
                    "ru": "Выполните операцию для разрешённой и ограниченной ролей, указанных в цитатах.",
                    "kk": "Дәйексөздегі рұқсат етілген және шектелген рөлдермен әрекетті орындаңыз.",
                }[language]
            elif kind == "transition":
                check = {
                    "en": "Trigger the quoted event and inspect the resulting status and whether the operation was applied once.",
                    "ru": "Вызовите указанное событие, проверьте итоговый статус и число обработок операции.",
                    "kk": "Көрсетілген оқиғаны іске қосып, соңғы мәртебені және орындалу санын тексеріңіз.",
                }[language]
            if conditional:
                expected = (item.get("suggestion") or item.get("default") or expected)
                if alternatives:
                    expected = {
                        "en": "Approval required. Compare the outcomes under each quoted rule at every listed input.",
                        "ru": "Требуется решение. Сопоставьте результаты по каждому процитированному правилу на всех указанных входах.",
                        "kk": "Мақұлдау қажет. Әр кіріс үшін дәйексөздегі әр ереже бойынша нәтижелерді салыстырыңыз.",
                    }[language]
            else:
                expected = condition
        scenarios.append({
            "id": f"T-{len(scenarios) + 1:03d}", "finding_id": item["id"], "title": item["title"],
            "severity": item["severity"], "evidence": item["evidence"],
            "condition": condition, "check": check, "expected": expected,
            "requirement_ids": item.get("requirement_ids", []), "inputs": inputs,
            "approval": "clarification" if conditional else "pending",
            "type": "boundary" if inputs else kind,
        })
    for req in requirements:
        if req.get("finding_ids") or not req.get("file_id") or req.get("status") != "clear":
            continue
        scenarios.append({
            "id": f"T-{len(scenarios) + 1:03d}", "finding_id": None,
            "title": f"{req['id']} · {req['text'][:100]}", "severity": "Low",
            "evidence": [req], "requirement_ids": [req["id"]], "inputs": [],
            "condition": req["text"], "approval": "pending", "type": "positive",
            "check": {"en": "Perform the action under the conditions in the requirement.",
                      "ru": "Выполните действие в условиях, заданных в требовании.",
                      "kk": "Талаптағы шарттарда әрекетті орындаңыз."}[language],
            "expected": req["text"],
        })
    critical_count = sum(row["severity"] == "Critical" for row in [*verified, *sanity])
    suggestions = list(dict.fromkeys([item["suggestion"] for item in verified] + [item["default"] for item in sanity]))
    return {
        "ambiguities": ambiguities, "sanity": sanity, "questions": questions, "scenarios": scenarios,
        "summaries": {
            "lead": {"text": copy["lead"].format(high=critical_count, questions=len(questions)), "items": [item["question"] for item in questions[:3]]},
            "developer": {"text": copy["developer"].format(count=len(suggestions)), "items": suggestions[:3]},
            "tester": {"text": copy["tester"].format(scenarios=len(scenarios)), "items": [item["expected"] for item in scenarios[:3]]},
        },
        "checked_criteria": insight_criteria(profile, mode),
    }
