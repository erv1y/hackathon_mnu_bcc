"""Проверки явно записанных правил ТЗ. Работают по тексту, без API-вызовов."""

import re
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations

from audit_documents import DocumentBundle

GENERAL_RULES = ("safe_method",)
DEEP_RULES = ("refund_minimum", "disjoint_intervals", "purchase_month", "notification_limit")

_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
_API_SECTION = re.compile(r"\b(?:REST API|HTTP API)\b", re.I)
_METHOD = re.compile(
    r"^\s*\|\s*(?:\*\*|`)?(?:REST type|HTTP method|HTTP-метод)(?:\*\*|`)?\s*"
    r"\|\s*`?(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)`?\s*\|", re.I,
)
_WRITE = re.compile(
    r"^\s*(?:[-*]|\d+[.)])?\s*(?:(?:при успешном выполнении|on success[:,]?|сәтті орындалғанда)\s+)?"
    r"(?:заменить|сохранить|удалить|создать|обновить|изменить|replace|save|delete|create|update)\s+"
    r"(?:настройк\w*|запис\w*|данные|ресурс\w*|остаток|(?:the\s+)?(?:settings|record|resource|data|balance))\b", re.I,
)
_ROUTE = re.compile(r"`(/[a-zA-Z][\w-]+(?:/[\w-]+)+)/?`")
_MONEY = r"(\d+(?:[ \u00a0\u202f]\d{3})*)\s*(?:тенге|теңге|KZT\b|₸)"
_REFUND_MINIMUM = re.compile(
    r"(?:сумма\s+(?:одного\s+)?возврата\s+должна\s+быть\s+не\s+меньше\s+"
    r"|минимальная\s+сумма\s+(?:одного\s+)?возврата(?:\s+составляет|\s*[:=])?\s+"
    r"|minimum\s+refund\s+amount(?:\s+is|\s*[:=])?\s+)" + _MONEY, re.I,
)
_INTERVAL = re.compile(
    r"^\s*[-*]?\s*([\w -]{3,70}):\s*(\d{1,12})\s*(?:–|—|\.\.)\s*(\d{1,12})\s*([\w%₸$]+)\s*\.?$",
    re.I,
)
_MONTHS = "январ феврал март апрел ма июн июл август сентябр октябр ноябр декабр".split()
_MONTH = r"(" + "|".join(_MONTHS) + r")\w*"
_PURCHASE = re.compile(r"\bпокупка\s+\d{1,2}\s+" + _MONTH, re.I)
_POSTED = re.compile(r"\bпровести\s+\d{1,2}\s+" + _MONTH, re.I)
_COUNTER = re.compile(r"^\s*" + _MONTH + r"\s+сч[её]тчик\s+(?:вырос|увеличил\w*)\b", re.I)
_MONTH_RULE = re.compile(r"месяц\s+(?:уч[её]та\s+)?определяется\s+по\s+дате\s+совершения\s+покупки", re.I)
_MONTH_EXCEPTION = re.compile(r"(?:месяц[^.]{0,75}по дате проведен|исключени\w+ (?:для|при) (?:отлож|поздн|провод|покуп))", re.I)
_QUOTA = re.compile(r"\bна (?:карту|сч[её]т|клиента) приходится максимум один (?:такой )?(?:push|пуш) за календарный месяц", re.I)
_CHANGE = re.compile(r"при изменении порога.*следующее достижение вызывает (?:push|пуш) независимо от истории предыдущей настройки", re.I)
_QUOTA_EXCEPTION = re.compile(r"(?:месячн\w+ лимит|лимит[^.]{0,35}месяц)[^.]{0,75}(?:сбрасывается|сохраняется|имеет приоритет|не действует)", re.I)


@dataclass(frozen=True)
class Line:
    start: int
    end: int
    text: str
    section: int | None
    api: int | None


def _lines(text: str) -> list[Line]:
    result = []
    headings: list[tuple[int, int, bool]] = []
    fence = ""
    for row in re.finditer(r"[^\n]+", text):
        value = row.group()
        opening = re.match(r"^\s*(`{3,}|~{3,})", value)
        if opening:
            marker = opening[1][0]
            fence = "" if fence == marker else fence or marker
            continue
        if fence:
            continue
        heading = _HEADING.match(value)
        if heading:
            depth = len(heading[1])
            headings = [item for item in headings if item[0] < depth]
            headings.append((depth, row.start(), bool(_API_SECTION.search(heading[2]))))
        else:
            api = next((start for _, start, is_api in reversed(headings) if is_api), None)
            result.append(Line(row.start(), row.end(), value, headings[-1][1] if headings else None, api))
    return result


def _same_document(lines: list[Line], deep: bool):
    sections = defaultdict(list)
    for line in lines:
        if line.api is not None:
            sections[line.api].append(line)
    for rows in sections.values():
        methods = [(row, match[1].upper()) for row in rows if (match := _METHOD.match(row.text))]
        write = next((row for row in rows if _WRITE.match(row.text)), None)
        if len(methods) == 1 and methods[0][1] == "GET" and write:
            yield "safe_method", (methods[0][0], write)

    if not deep:
        return
    intervals = defaultdict(list)
    for row in lines:
        bounds = _INTERVAL.fullmatch(row.text)
        if bounds and row.section is not None and int(bounds[2]) <= int(bounds[3]):
            key = (row.section, " ".join(bounds[1].casefold().split()), bounds[4].casefold())
            intervals[key].append((int(bounds[2]), int(bounds[3]), row))
    for values in intervals.values():
        highest_start = max(values, key=lambda item: item[0])
        lowest_end = min(values, key=lambda item: item[1])
        if highest_start[0] > lowest_end[1]:
            yield "disjoint_intervals", (lowest_end[2], highest_start[2])

    scope = "\n".join(row.text for row in lines)
    month = next((row for row in lines if _MONTH_RULE.search(row.text)), None)
    if month and not _MONTH_EXCEPTION.search(scope):
        for row in lines:
            cells = row.text.strip().strip("|").split("|")
            if len(cells) != 3:
                continue
            purchase, posted, counter = _PURCHASE.search(cells[0]), _POSTED.search(cells[1]), _COUNTER.match(cells[2])
            if purchase and posted and counter and purchase[1].lower() != posted[1].lower() == counter[1].lower():
                yield "purchase_month", (month, row)
                break

    cap = next((row for row in lines if _QUOTA.search(row.text)), None)
    if cap and cap.section is not None and not _QUOTA_EXCEPTION.search(scope):
        change = next((row for row in lines if row.section == cap.section and _CHANGE.search(row.text)), None)
        if change:
            yield "notification_limit", (cap, change)


def detect_contract_checks(bundle: DocumentBundle, *, deep: bool) -> list[tuple[str, list[dict]]]:
    found = []
    refund_bounds = defaultdict(list)
    for index, source in enumerate(bundle.sources):
        lines = _lines(source.text)
        for rule, rows in _same_document(lines, deep):
            found.append((rule, [bundle.excerpt(index, row.start, row.end) for row in rows]))
            if len(found) >= 32:
                return found
        if not deep or source.role == "checklist":
            continue
        routes = {route for row in lines for route in _ROUTE.findall(row.text)}
        bounds = {
            int(re.sub(r"\s+", "", match[1])): row
            for row in lines if (match := _REFUND_MINIMUM.search(row.text)) and len(match[1]) <= 24
        }
        if len(routes) == 1 and len(bounds) == 1:
            amount, row = next(iter(bounds.items()))
            refund_bounds[routes.pop()].append((amount, index, row))
    for values in refund_bounds.values():
        conflict = next(((a, b) for a, b in combinations(values, 2) if a[0] != b[0]), None)
        if conflict:
            found.append(("refund_minimum", [bundle.excerpt(index, row.start, row.end) for _, index, row in conflict]))
    return found[:32]
