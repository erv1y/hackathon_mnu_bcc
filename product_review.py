"""Conservative product projections over the existing evidence-backed report."""
import re
from decimal import Decimal, InvalidOperation

from schemas import SEVERITIES

_NUMERIC = re.compile(r"(?<![\w-])\d+(?:[ ,\u00a0]\d{3})*(?:[.,]\d+)?(?![\w-])")


def normalized(value):
    return " ".join(str(value or "").casefold().split())


def group_findings(findings):
    groups = {}
    for row in findings:
        evidence = tuple(sorted({
            (str(q.get("file_id") or q.get("file_name") or ""), str(q.get("start")), str(q.get("end")),
             normalized(q["text"])) for q in row.get("evidence", [])
        }))
        substance = tuple(normalized(row.get(key)) for key in ("issue", "suggestion"))
        key = (row.get("criterion_id"), tuple(sorted(row.get("requirement_ids", []))), evidence,
               substance, row.get("review_reason"), row.get("action_kind"))
        if row.get("verified") != "exact" or not evidence or not all(substance):
            key = (row["id"],)
        groups.setdefault(key, []).append(row)
    return [sorted(rows, key=lambda row: SEVERITIES.index(row["severity"])) for rows in groups.values()]


def grounded_options(evidence):
    result = []
    units = set()
    for proof in evidence:
        matches = list(_NUMERIC.finditer(proof["text"]))
        if len(matches) != 1:
            return []
        match = matches[0]
        suffix = proof["text"][match.end():].lstrip().casefold()
        unit = re.match(r"[^\W\d_]+|[₸$€%]", suffix)
        units.add(unit[0] if unit else "")
        if match[0] not in result:
            result.append(match[0])
    return result if len(result) > 1 and len(units) == 1 else []


def boundary_inputs(values):
    inputs = []
    for value in values:
        if re.fullmatch(r"[0-9]+(?:[ ,\u00a0][0-9]{3})*", value) is None:
            continue
        try:
            number = Decimal(re.sub(r"[ ,\u00a0]", "", value))
        except InvalidOperation:
            continue
        inputs.extend(str(candidate) for candidate in (number - 1, number, number + 1))
    return list(dict.fromkeys(inputs))
