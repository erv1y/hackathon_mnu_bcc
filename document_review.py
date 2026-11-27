"""Карта требований и безопасные изменения в координатах исходного текста."""

import re

from schemas import SEVERITIES

PRIORITIES = {"Critical": "P0", "High": "P0", "Medium": "P1", "Low": "P2"}
_SEVERITY_INDEX = {key: index for index, key in enumerate(SEVERITIES)}
_LIST_ITEM = re.compile(r"^\s*(?:[-*+•]\s+|\d+(?:\.\d+)*[.)]\s+|[A-Za-zА-ЯЁа-яё]{1,8}-\d+\s*[:.)]\s*)")
_SOURCE_ID = re.compile(r"^\s*(?:[-*+•]\s+)?\*{0,2}([^\W\d_]{1,12}[-–]\d+(?:\.\d+)*)[.):\s*]")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+)$")
_TABLE_RULE = re.compile(r"^\s*\|?[\s|:-]+\|?\s*$")
_CONTEXT_SECTIONS = {"общие сведения", "ревью требований", "блок согласующих"}
_TABLE_METADATA = {"author", "developer", "swagger", "gitlab project", "curl", "автор", "разработчик"}
_CONNECTORS = {"или", "or", "немесе"}
_DECLARED_PRIORITY = re.compile(r"(?<!\w)P[0-2](?!\w)", re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_SECURITY = re.compile(
    r"cvv|cvc|encrypt|password|authentication|personal data|access right|"
    r"шифр|парол|аутентиф|персональн|личн\w+\s+данн|прав\w+\s+доступа|код.{0,24}карт|"
    r"құпиясөз|қауіпсіздік|дербес дерек|жеке дерек"
)
_QUALITY = re.compile(
    r"\b(?:sla|slo|p95|p99|uptime|latency|performance|throughput)\b|"
    r"seconds?|response time|load capacity|reasonably quickly|"
    r"быстр|производительност|пропускн\w+\s+способност|врем\w+\s+ответа|задержк|секунд|"
    r"жылдам|өнімділік|секунд|жауап уақыты"
)


def numeric_tokens(text: str) -> set[str]:
    return {value.replace(",", ".") for value in _NUMBER.findall(text)}


def _requirement_kind(text: str) -> str:
    value = text.casefold()
    if _SECURITY.search(value):
        return "security"
    if _QUALITY.search(value):
        return "quality"
    return "functional"


def structure_document(source: str) -> list[dict]:
    """Привязывает карточки к строкам и смещениям, не переписывая документ."""
    requirements: list[dict] = []
    block: list[tuple[int, int, str]] = []
    sections: list[tuple[int, str]] = []
    fenced = False
    table_columns = ""

    def finish() -> None:
        if not block:
            return
        line_start, start, first = block[0]
        line_end, last_start, last = block[-1]
        start += len(first) - len(first.lstrip())
        end = last_start + len(last.rstrip())
        text = source[start:end]
        priority = _DECLARED_PRIORITY.search(text)
        source_id = _SOURCE_ID.match(text)
        requirements.append({
            "id": f"REQ-{len(requirements) + 1:03d}",
            "source_id": source_id.group(1) if source_id else None,
            "kind": _requirement_kind(text),
            "section": " / ".join(title for level, title in sections if level > 1),
            "table_columns": table_columns,
            "start": start,
            "end": end,
            "line_start": line_start,
            "line_end": line_end,
            "priority": priority.group().upper() if priority else None,
            "priority_source": "document" if priority else None,
            "finding_ids": [],
            "risk_count": 0,
        })
        block.clear()

    offset = 0
    lines = source.splitlines(keepends=True)
    has_sections = any((heading := _HEADING.match(line)) and len(heading.group(1)) == 2 for line in lines)
    for index, line in enumerate(lines, start=1):
        content = line.rstrip("\r\n")
        heading = _HEADING.match(content)
        is_table_row = content.lstrip().startswith("|")
        next_line = lines[index] if index < len(lines) else ""
        if content.lstrip().startswith(("~~~", chr(96) * 3)):
            finish()
            fenced = not fenced
            table_columns = ""
        elif fenced:
            pass
        elif heading:
            finish()
            level, title = len(heading.group(1)), heading.group(2).strip()
            sections = [part for part in sections if part[0] < level]
            sections.append((level, title))
            table_columns = ""
        elif (not content.strip() or _TABLE_RULE.fullmatch(content)
              or any(title.casefold() in _CONTEXT_SECTIONS for _, title in sections)
              or (has_sections and sections and sections[-1][0] == 1)):
            finish()
            if not is_table_row:
                table_columns = ""
        elif is_table_row and _TABLE_RULE.fullmatch(next_line) and "|" in next_line:
            finish()
            table_columns = " · ".join(cell.strip() for cell in content.strip().strip("|").split("|"))
        elif is_table_row:
            finish()
            field = content.lstrip().split("|")[1].strip().strip("*").strip().casefold()
            if field not in _TABLE_METADATA:
                block.append((index, offset, content))
                finish()
        else:
            previous = block[-1][2] if block else ""
            table_columns = ""
            if _LIST_ITEM.match(content) or "|" in content or "|" in previous or previous.rstrip().endswith((".", "!", "?")):
                finish()
            if content.strip().casefold() not in _CONNECTORS:
                block.append((index, offset, content))
        offset += len(line)
    finish()
    return requirements


def _fix_plan(defect: dict) -> dict:
    reason = defect.get("review_reason")
    quotes = defect["evidence"]
    action = defect.get("fix_kind", "clarify")
    question = defect.get("clarification", "")
    if reason:
        return {"kind": "review", "question": question}
    if defect["verified"] != "exact" or len(quotes) != 1 or len(defect["requirement_ids"]) != 1:
        return {"kind": "clarify", "question": question}
    if action != "rewrite":
        return {"kind": "clarify", "question": question}

    quote = quotes[0]
    replacement = defect["suggestion"].strip()
    if not replacement or replacement == quote["text"] or numeric_tokens(replacement) - numeric_tokens(quote["text"]):
        return {"kind": "clarify", "question": question}
    return {
        "kind": "replace",
        "start": quote["start"],
        "end": quote["end"],
        "original": quote["text"],
        "replacement": replacement,
    }


def document_review(source: str, modules: list[dict], *, complete: bool, bundle=None) -> dict:
    requirements = bundle.requirements() if bundle else structure_document(source)
    findings = [finding for module in modules for finding in module["defects"]]
    linked: dict[str, list[dict]] = {item["id"]: [] for item in requirements}
    unlinked = []
    for index, finding in enumerate(findings, start=1):
        finding["id"] = f"F-{index:03d}"
        finding["requirement_ids"] = [
            item["id"] for item in requirements
            if any(item["start"] < proof["end"] and proof["start"] < item["end"] for proof in finding["evidence"])
        ]
        for key in finding["requirement_ids"]:
            linked[key].append(finding)
        if not finding["requirement_ids"]:
            unlinked.append(finding["id"])
        finding["fix"] = _fix_plan(finding)

    for item in requirements:
        attached = linked[item["id"]]
        counted = [finding for finding in attached if not finding.get("review_reason")]
        item["finding_ids"] = [finding["id"] for finding in attached]
        item["risk_count"] = len(counted)
        item["severity"] = min((finding["severity"] for finding in counted), key=_SEVERITY_INDEX.get, default=None)
        if item["priority"] is None and item["severity"]:
            item["priority"] = PRIORITIES[item["severity"]]
            item["priority_source"] = "audit"
        if counted:
            item["status"] = "risk"
        elif attached:
            item["status"] = "review"
        else:
            item["status"] = "clear" if complete else "partial"

    return {
        "text": source, "requirements": requirements, "unmapped_findings": unlinked,
        "files": bundle.manifest if bundle else [],
    }
