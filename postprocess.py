"""Проверка доказательств и объединение находок без вызова модели."""

import re
from bisect import bisect_right
from difflib import SequenceMatcher

from audit_profile import AuditProfile
from languages import MISSING_QUOTES, Language
from schemas import CheckMode, SEVERITIES

SEV_RANK = {severity: index for index, severity in enumerate(SEVERITIES)}
_QUOTES = str.maketrans("«»“”„‘’", '"""""' + "''")
_SPACE = re.compile(r"\s+")
_PARTS = re.compile(r"\s+|\S+")
_MISSING = {marker.casefold() for marker in MISSING_QUOTES.values()}


def _canonical(text: str) -> str:
    return _SPACE.sub(" ", text.translate(_QUOTES)).strip()


class QuoteIndex:
    """Сопоставляет нормализованную цитату с координатами извлечённого ТЗ."""

    def __init__(self, source: str):
        self.source = source
        self.offsets: list[int] = []
        self.line_starts = [0, *(match.end() for match in re.finditer("\n", source))]
        parts = []
        for match in _PARTS.finditer(source):
            value = match.group()
            if value.isspace():
                parts.append(" ")
                self.offsets.append(match.start())
            else:
                parts.append(value.translate(_QUOTES))
                self.offsets.extend(range(match.start(), match.end()))
        self.text = "".join(parts)

    def match(self, quote: str, file_id: str = "") -> dict | None:
        if file_id and file_id != "D1":
            return None
        normalized = _canonical(quote)
        candidates = [normalized]
        if len(normalized) > 2 and normalized[0] == normalized[-1] and normalized[0] in "\"'":
            candidates.append(normalized[1:-1].strip())
        for candidate in candidates:
            if len(candidate) < 8:
                continue
            # Не считаем совпадением кусок слова или только начало длинной цитаты.
            pattern = re.escape(candidate)
            if candidate[0].isalnum():
                pattern = r"(?<!\w)" + pattern
            if candidate[-1].isalnum():
                pattern += r"(?!\w)"
            found = re.search(pattern, self.text)
            if found is None:
                continue
            start = self.offsets[found.start()]
            end = self.offsets[found.end() - 1] + 1
            return {
                "text": self.source[start:end],
                "line_start": bisect_right(self.line_starts, start),
                "line_end": bisect_right(self.line_starts, end - 1),
                "start": start,
                "end": end,
            }
        return None


class BundleQuoteIndex:
    def __init__(self, bundle):
        self.bundle = bundle
        self.indexes = [QuoteIndex(item.text) for item in bundle.sources]

    def match(self, quote: str, file_id: str = "") -> dict | None:
        matches = []
        for pos, index in enumerate(self.indexes):
            if not file_id or self.bundle.manifest[pos]["id"] == file_id:
                proof = index.match(quote)
                if proof:
                    matches.append(self.bundle.point(pos, proof))
        return matches[0] if len(matches) == 1 else None


def verify_quote(spec: str, quote: str) -> str:
    if _canonical(quote).casefold() in _MISSING:
        return "missing"
    return "exact" if QuoteIndex(spec).match(quote) else "unverified"


def _overlap(a: dict, b: dict) -> bool:
    return a["start"] < b["end"] and b["start"] < a["end"]


def _text_similarity(a: str, b: str) -> float:
    a, b = _canonical(a).casefold(), _canonical(b).casefold()
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def _same(a: dict, b: dict) -> bool:
    if a["verified"] != b["verified"] or a.get("criterion_id", "") != b.get("criterion_id", ""):
        return False
    if a["criterion"] != b["criterion"]:
        return False
    if a["verified"] == "exact":
        left, right = a["evidence"], b["evidence"]
        if len(left) != len(right) or not all(any(_overlap(x, y) for y in right) for x in left):
            return False
        return _text_similarity(a["title"], b["title"]) >= 0.9 or _text_similarity(a["issue"], b["issue"]) >= 0.9
    if a["verified"] == "missing":
        return _text_similarity(a["title"], b["title"]) >= 0.95 and _text_similarity(a["issue"], b["issue"]) >= 0.85
    return False


def _add_evidence(defect: dict, index: QuoteIndex | BundleQuoteIndex, min_quotes: int) -> None:
    quote = defect.get("quote", "")
    related = defect.get("related_quotes", [])
    defect["evidence"] = []
    if _canonical(quote).casefold() in _MISSING and not related and min_quotes == 1:
        defect["verified"] = "missing"
        defect["evidence_issue"] = None
        return

    all_found = True
    ids = defect.get("related_file_ids", [])
    for pos, fragment in enumerate([quote, *related]):
        file_id = defect.get("file_id", "") if pos == 0 else ids[pos - 1] if pos <= len(ids) else ""
        evidence = index.match(fragment, file_id)
        if evidence is None:
            all_found = False
        elif not any(_overlap(evidence, previous) for previous in defect["evidence"]):
            defect["evidence"].append(evidence)
    if not all_found:
        defect["evidence_issue"] = "quote_not_found"
    elif len(defect["evidence"]) < min_quotes:
        defect["evidence_issue"] = "not_enough_quotes"
    else:
        defect["evidence_issue"] = None
    defect["verified"] = "unverified" if defect["evidence_issue"] else "exact"


def refine(outcomes: dict[str, dict], spec: str, profile: AuditProfile | None = None,
           language: Language = "ru", *, bundle=None, check_mode: CheckMode = "deep") -> int:
    """Обрабатывает секции в порядке профиля и сохраняет высшую критичность дублей."""
    index = BundleQuoteIndex(bundle) if bundle else QuoteIndex(spec)
    scope = profile.check_criteria(check_mode) if profile else {}
    kept: list[tuple[dict, str]] = []
    merged = 0
    for agent_key, outcome in outcomes.items():
        if not outcome.get("ok"):
            continue
        criteria = {key: rule for key, rule in scope.items() if agent_key in rule.agents}
        unique = []
        for defect in sorted(outcome["defects"], key=lambda d: SEV_RANK[d["severity"]]):
            criterion_id = defect.get("criterion_id", "")
            criterion = criteria.get(criterion_id)
            defect["criterion"] = (
                {"id": criterion_id, "title": profile.criterion_title(criterion_id, language)}
                if profile and criterion else None
            )
            _add_evidence(defect, index, criterion.min_quotes if criterion else 1)
            defect["references"] = [
                {"title": profile.reference_title(key, language), "url": str(profile.references[key].url)}
                for key in criterion.references
                if defect["verified"] != "unverified"
            ] if profile and criterion else []
            defect["refs"] = [reference["title"] for reference in defect["references"]]
            defect["also_by"] = []
            twin = next(((first, owner) for first, owner in kept if _same(first, defect)), None)
            if twin is None:
                unique.append(defect)
                kept.append((defect, agent_key))
                continue

            first, owner = twin
            if SEV_RANK[defect["severity"]] < SEV_RANK[first["severity"]]:
                first["severity"] = defect["severity"]
            if owner != agent_key and outcome["title"] not in first["also_by"]:
                first["also_by"].append(outcome["title"])
            merged += 1
        outcome["defects"] = unique
    return merged
