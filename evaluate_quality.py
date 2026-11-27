"""Замер качества проверки отдельных требований и документа целиком."""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Literal

from pydantic import Field

from analyzer import ConfigError, MISSING_KEY_MESSAGE, get_settings, is_llm_configured
from audit_profile import BASE_DIR, DataFileError, DataModel, Slug, read_data
from document_review import structure_document
from pruvia import run_quality_audit
from quality_audit import RUBRIC_PATH, QualityRubric, load_quality_rubric

DATASET_PATH = BASE_DIR / "evaluation" / "official-cases.json"


class Label(DataModel):
    requirement: str = Field(pattern=r"^(SET|REQ-\d{3})$")
    criterion: str = Field(min_length=1, max_length=80)
    status: Literal["pass", "fail", "na"]
    anchors: list[str] = Field(default_factory=list, max_length=3)


class Case(DataModel):
    id: Slug
    text: str = Field(min_length=8, max_length=60000)
    labels: list[Label] = Field(min_length=1)


class Dataset(DataModel):
    id: Slug
    version: str
    cases: list[Case] = Field(min_length=1)


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def validate_dataset(dataset: Dataset, rubric: QualityRubric) -> None:
    if len({case.id for case in dataset.cases}) != len(dataset.cases):
        raise DataFileError("ID примеров должны быть уникальны.")
    for case in dataset.cases:
        sources = {item["id"]: case.text[item["start"]:item["end"]] for item in structure_document(case.text)}
        sources["SET"] = case.text
        seen = set()
        for label in case.labels:
            rules = rubric.whole if label.requirement == "SET" else rubric.individual
            scope = (label.requirement, label.criterion)
            if scope in seen or label.requirement not in sources or label.criterion not in rules:
                raise DataFileError(f"{case.id}: проверьте ID требования и критерия {scope}.")
            seen.add(scope)
            if (label.status == "fail") != bool(label.anchors):
                raise DataFileError(f"{case.id}: фрагменты обязательны для нарушения {scope}.")
            source = _normalize(sources[label.requirement])
            if any(not anchor.strip() or _normalize(anchor) not in source for anchor in label.anchors):
                raise DataFileError(f"{case.id}: фрагмент для {scope} отсутствует в источнике.")


def measure(case: Case, review: dict) -> list[dict]:
    scopes = {item["id"]: item["checks"] for item in review["requirements"]}
    scopes["SET"] = review["whole"]
    measured = []
    for label in case.labels:
        cell = scopes.get(label.requirement, {}).get(label.criterion, {"status": "unknown", "findings": []})
        anchors = [_normalize(value) for value in label.anchors]
        grounded = not anchors or any(
            all(any(anchor in _normalize(proof["text"]) for proof in item["evidence"]) for anchor in anchors)
            for item in cell["findings"]
        )
        measured.append({
            "case": case.id, "requirement": label.requirement, "criterion": label.criterion,
            "expected": label.status, "actual": cell["status"],
            "match": label.status == cell["status"] and grounded,
        })
    return measured


async def _measure_cases(dataset: Dataset, rubric: QualityRubric, settings) -> list[dict]:
    results = []
    for case in dataset.cases:
        results.extend(measure(case, await run_quality_audit(case.text, settings, rubric)))
    return results


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--dataset", type=Path, default=DATASET_PATH)
    cli.add_argument("--rubric", type=Path, default=RUBRIC_PATH)
    cli.add_argument("--live", action="store_true")
    cli.add_argument("--mode", choices=("quick", "deep"), default="deep", help="охват проверки")
    cli.add_argument("--load-dotenv", action="store_true")
    cli.add_argument("--out", type=Path, help="сохранить метрики и оценки в JSON")
    args = cli.parse_args()
    if (args.load_dotenv or args.out) and not args.live:
        cli.error("--load-dotenv и --out требуют --live")
    try:
        dataset = read_data(args.dataset, Dataset)
        base_rubric = load_quality_rubric(args.rubric)
        validate_dataset(dataset, base_rubric)
        rubric = base_rubric.for_mode(args.mode)
        count = sum(
            label.criterion in (rubric.whole if label.requirement == "SET" else rubric.individual)
            for case in dataset.cases for label in case.labels
        )
        if not count:
            raise DataFileError("Для выбранного режима нет размеченных оценок.")
        print(f"{dataset.id} v{dataset.version} · {len(dataset.cases)} документов · {count} размеченных оценок · {args.mode}")
        if not args.live:
            return 0
        if args.load_dotenv:
            from dotenv import load_dotenv
            load_dotenv(BASE_DIR / ".env")
        settings = get_settings()
        if not is_llm_configured(settings):
            raise ConfigError(MISSING_KEY_MESSAGE)
        rows = [
            row for row in asyncio.run(_measure_cases(dataset, rubric, settings))
            if row["criterion"] in (rubric.whole if row["requirement"] == "SET" else rubric.individual)
        ]
        defects = [row for row in rows if row["expected"] == "fail"]
        metrics = {
            "decision_coverage": sum(row["actual"] != "unknown" for row in rows) / count,
            "accuracy": sum(row["match"] for row in rows) / count,
            "grounded_defect_recall": sum(row["match"] for row in defects) / len(defects) if defects else None,
            "false_alarms": sum(row["actual"] == "fail" and not row["match"] for row in rows),
        }
        report = {
            "dataset": dataset.fingerprint, "rubric": rubric.fingerprint, "check_mode": rubric.mode, "model": settings.model,
            "provider": settings.provider, "metrics": metrics, "labels": rows,
        }
        print(json.dumps(metrics, ensure_ascii=False, indent=2))
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return int(metrics["decision_coverage"] < 1)
    except (OSError, ConfigError, DataFileError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
