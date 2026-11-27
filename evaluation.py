"""Разметка, сохранённые ответы и метрики без дополнительного вызова модели."""

import re
from collections import Counter
from pathlib import Path
from time import perf_counter
from typing import Annotated, Literal

from pydantic import Field, model_validator

from analyzer import Settings, build_result
from audit_profile import BASE_DIR, CriterionID, DataFileError, DataModel, ShortText, Slug, read_data
from languages import Language
from parser import normalize_text
from postprocess import QuoteIndex
from schemas import Defect, Severity

CASES_PATH = BASE_DIR / "evaluation" / "fintech-cases.json"
REPLAY_PATH = BASE_DIR / "evaluation" / "fintech-replay.json"
MetricName = Literal[
    "precision", "recall", "f1", "high_recall", "severity_accuracy",
    "verified_quote_rate", "agent_success_rate", "clean_pass_rate",
]
METRIC_LABELS = {
    "precision": "Точность по разметке",
    "recall": "Полнота по разметке",
    "f1": "F1",
    "high_recall": "Полнота критичных рисков",
    "severity_accuracy": "Совпадение критичности",
    "verified_quote_rate": "Доля найденных цитат",
    "agent_success_rate": "Проверки без сбоя",
    "clean_pass_rate": "Чистые примеры без находок",
}


class ExpectedFinding(DataModel):
    criterion_id: CriterionID
    severity: Severity
    evidence: Literal["exact", "missing"]
    anchors: list[Annotated[str, Field(min_length=8, max_length=300)]] = Field(
        default_factory=list, max_length=3
    )

    @model_validator(mode="after")
    def check_anchors(self):
        if (self.evidence == "exact") != bool(self.anchors):
            raise ValueError("Для exact нужны anchors, для missing они не указываются")
        if len(self.anchors) != len(set(self.anchors)):
            raise ValueError("Доказательства должны относиться к разным фрагментам")
        return self


class EvalCase(DataModel):
    id: Slug
    title: ShortText
    language: Language = "ru"
    text: Annotated[str, Field(min_length=40, max_length=60000)]
    context: str = Field(default="", max_length=5000)
    expected: list[ExpectedFinding] = Field(max_length=64)

    @model_validator(mode="after")
    def check_annotation(self):
        index = QuoteIndex(normalize_text(self.text + "\n\n" + getattr(self, "companion", "")))
        seen = set()
        for expected in self.expected:
            key = (expected.criterion_id, expected.evidence, tuple(sorted(expected.anchors)))
            if key in seen:
                raise ValueError(f"{self.id}: повтор разметки для {expected.criterion_id}")
            seen.add(key)
            if any(index.match(anchor) is None for anchor in expected.anchors):
                raise ValueError(f"{self.id}: одна из цитат разметки отсутствует в ТЗ ({expected.criterion_id})")
        return self


class DemoCases(DataModel):
    before: Slug
    after: Slug


class EvalSuite(DataModel):
    id: Slug
    version: Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]
    cases: list[EvalCase] = Field(min_length=1, max_length=128)
    gates: dict[MetricName, Annotated[float, Field(ge=0, le=1)]] = Field(default_factory=dict)
    demo: dict[Language, DemoCases] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_case_ids(self):
        by_id = {case.id: case for case in self.cases}
        if len(by_id) != len(self.cases):
            raise ValueError("ID примеров повторяются")
        for language, pair in self.demo.items():
            if pair.before == pair.after or {pair.before, pair.after} - by_id.keys():
                raise ValueError(f"{language}: для демо нужны два разных ID из cases")
            if any(by_id[key].language != language for key in (pair.before, pair.after)):
                raise ValueError(f"{language}: язык примера должен совпадать с языком демо")
        return self


class ReplayPack(DataModel):
    dataset_id: Slug
    dataset_version: str
    profile_id: Slug
    profile_version: str
    observations: dict[Slug, dict[Slug, list[Defect]]]


def load_suite(path: Path = CASES_PATH) -> EvalSuite:
    return read_data(path, EvalSuite)


def load_replay(path: Path = REPLAY_PATH) -> ReplayPack:
    return read_data(path, ReplayPack)


def validate_suite(suite: EvalSuite, settings: Settings) -> None:
    unknown = {
        finding.criterion_id for case in suite.cases for finding in case.expected
        if finding.criterion_id not in settings.profile.criteria
    }
    if unknown:
        raise DataFileError(f"В профиле нет критериев из разметки: {', '.join(sorted(unknown))}.")


def validate_replay(replay: ReplayPack, suite: EvalSuite, settings: Settings) -> None:
    profile = settings.profile
    if (replay.dataset_id, replay.dataset_version) != (suite.id, suite.version):
        raise DataFileError("Пакет ответов относится к другой версии набора примеров.")
    if (replay.profile_id, replay.profile_version) != (profile.id, profile.version):
        raise DataFileError("Пакет ответов относится к другой версии профиля.")
    if set(replay.observations) != {case.id for case in suite.cases}:
        raise DataFileError("ID примеров и сохранённых прогонов должны совпадать.")
    for case_id, observations in replay.observations.items():
        if set(observations) != set(profile.agents):
            raise DataFileError(f"{case_id}: в сохранённом прогоне нужен список находок для каждого агента.")


def replay_report(case: EvalCase, replay: ReplayPack, settings: Settings, *, check_mode="deep") -> dict:
    started = perf_counter()
    observations = replay.observations[case.id]
    outcomes = {
        key: {
            "title": settings.profile.agents[key].title,
            "ok": True,
            "error": None,
            "status_code": 200,
            "elapsed_sec": 0,
            "defects": [finding.model_dump() for finding in observations[key][:settings.max_defects]],
        }
        for key in settings.enabled_agents
    }
    return build_result(
        outcomes, settings, normalize_text(case.text), perf_counter() - started, mode="replay",
        language=case.language, check_mode=check_mode,
    )


def _search_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).casefold()


def _matches(expected: ExpectedFinding, candidate: dict) -> bool:
    if not candidate.get("criterion") or expected.criterion_id != candidate["criterion_id"]:
        return False
    if expected.evidence != candidate["verified"]:
        return False
    quotes = [_search_text(fragment["text"]) for fragment in candidate["evidence"]]
    return all(any(_search_text(anchor) in quote for quote in quotes) for anchor in expected.anchors)


def _match_findings(expected: list[ExpectedFinding], found: list[dict]) -> dict[int, int]:
    # Максимальное сопоставление: одна находка не закрывает два замечания разметки.
    options = [
        sorted(
            (j for j, item in enumerate(found) if _matches(label, item)),
            key=lambda j: found[j]["severity"] != label.severity,
        )
        for label in expected
    ]
    owner: dict[int, int] = {}

    def assign(i: int, visited: set[int]) -> bool:
        for j in options[i]:
            if j in visited:
                continue
            visited.add(j)
            if j not in owner or assign(owner[j], visited):
                owner[j] = i
                return True
        return False

    order = sorted(range(len(expected)), key=lambda i: (expected[i].severity != "High", len(options[i])))
    for i in order:
        assign(i, set())
    return {i: j for j, i in owner.items()}


def measure_case(case: EvalCase, report: dict) -> dict:
    found = [defect for module in report["modules"] for defect in module["defects"]]
    matched = _match_findings(case.expected, found)
    used = set(matched.values())
    high_labels = [label for label in case.expected if label.severity == "High"]
    high_predictions = [item for item in found if item["severity"] == "High"]
    return {
        "id": case.id,
        "language": case.language,
        "expected": len(case.expected),
        "predicted": len(found),
        "matched": len(matched),
        "high_expected": len(high_labels),
        "high_matched": len(_match_findings(high_labels, high_predictions)),
        "severity_matches": sum(case.expected[i].severity == found[j]["severity"] for i, j in matched.items()),
        "quoted": sum(item["verified"] != "missing" for item in found),
        "verified": sum(item["verified"] == "exact" for item in found),
        "agents_completed": report["coverage"]["agents_completed"],
        "agents_total": report["coverage"]["agents_total"],
        "failed_agents": report["failed_agents"],
        "elapsed_sec": report["elapsed_sec"],
        "missed": [label.criterion_id for i, label in enumerate(case.expected) if i not in matched],
        "extra": [
            {"criterion_id": item["criterion_id"], "severity": item["severity"], "evidence": item["verified"]}
            for j, item in enumerate(found) if j not in used
        ],
    }


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def summarize(cases: list[dict]) -> dict:
    keys = (
        "expected", "predicted", "matched", "high_expected", "high_matched", "severity_matches",
        "quoted", "verified", "agents_completed", "agents_total",
    )
    totals = Counter()
    for case in cases:
        totals.update({key: case[key] for key in keys})
    t = totals
    clean = [case for case in cases if not case["expected"]]
    clean_ok = sum(not case["predicted"] and not case["failed_agents"] for case in clean)
    return {
        "precision": _ratio(t["matched"], t["predicted"]) if t["predicted"] else float(not t["expected"]),
        "recall": _ratio(t["matched"], t["expected"]),
        "f1": _ratio(2 * t["matched"], t["predicted"] + t["expected"]),
        "high_recall": _ratio(t["high_matched"], t["high_expected"]),
        "severity_accuracy": _ratio(t["severity_matches"], t["matched"]),
        "verified_quote_rate": _ratio(t["verified"], t["quoted"]),
        "agent_success_rate": _ratio(t["agents_completed"], t["agents_total"]),
        "clean_pass_rate": _ratio(clean_ok, len(clean)),
    }
