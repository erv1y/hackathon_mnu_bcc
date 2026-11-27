"""Конфигурация предметной области. Секреты и параметры провайдера сюда не входят."""

import hashlib
import os
from pathlib import Path
from typing import Annotated, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError, field_validator, model_validator

from languages import Language
from schemas import CheckMode, SEVERITIES, Severity

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_PROFILE = BASE_DIR / "profiles" / "fintech-kz.json"
Slug = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
CriterionID = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,79}$")]
ShortText = Annotated[str, Field(min_length=1, max_length=180)]
Instruction = Annotated[str, Field(min_length=1, max_length=5000)]
ReportField = Literal["evidence", "issue", "suggestion", "criterion", "references", "contributors"]


class DataFileError(ValueError):
    """Некорректный локальный JSON-файл."""


class DataModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode("utf-8")).hexdigest()


T = TypeVar("T", bound=DataModel)


def read_data(path: Path, model: type[T]) -> T:
    if path.suffix.lower() != ".json":
        raise DataFileError(f"Ожидается файл .json: {path.name}")
    try:
        raw = path.read_text(encoding="utf-8-sig")
        return model.model_validate_json(raw)
    except (OSError, UnicodeError) as exc:
        raise DataFileError(f"Не удалось прочитать {path.name}.") from exc
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_input=False)
        detail = "\n".join(
            f"  {'.'.join(map(str, error['loc'])) or 'JSON'}: {error['msg']}"
            for error in errors
        )
        raise DataFileError(f"Ошибка в {path.name}:\n{detail}") from None


class Agent(DataModel):
    title: ShortText
    scope: ShortText
    role: Instruction
    style: Literal["pay", "kyc", "sec", "logic"] = "logic"


class Criterion(DataModel):
    title: ShortText
    agents: list[Slug] = Field(min_length=1, max_length=8)
    rule: Instruction
    default_severity: Severity
    min_quotes: int = Field(default=1, ge=1, le=3, strict=True)
    references: list[Slug] = Field(default_factory=list, max_length=6)


class Reference(DataModel):
    title: ShortText
    url: HttpUrl

    @field_validator("url")
    @classmethod
    def require_https(cls, url: HttpUrl) -> HttpUrl:
        if url.scheme != "https" or url.username or url.password:
            raise ValueError("Используйте ссылку HTTPS без учётных данных")
        return url


class ScoreRules(DataModel):
    weights: dict[Severity, Annotated[int, Field(ge=0, le=100, strict=True)]]
    good_threshold: int = Field(default=80, ge=1, le=100, strict=True)
    warning_threshold: int = Field(default=50, ge=1, le=100, strict=True)
    count_missing: bool = True

    @model_validator(mode="after")
    def check_weights(self):
        if set(self.weights) not in (set(SEVERITIES), set(SEVERITIES) - {"Critical"}):
            raise ValueError("В weights нужны High, Medium и Low")
        if self.weights.get("Critical", self.weights["High"]) < self.weights["High"]:
            raise ValueError("Вес Critical должен быть не ниже High")
        if not self.weights["High"] >= self.weights["Medium"] >= self.weights["Low"]:
            raise ValueError("Вес High должен быть не ниже Medium, а Medium — не ниже Low")
        if self.warning_threshold >= self.good_threshold:
            raise ValueError("warning_threshold должен быть ниже good_threshold")
        return self


class ReportFormat(DataModel):
    title: ShortText
    filename: Slug
    group_by: Literal["agent", "severity"] = "agent"
    fields: list[ReportField] = Field(
        default_factory=lambda: ["evidence", "issue", "suggestion", "criterion", "references", "contributors"],
        min_length=1,
        max_length=6,
    )
    exports: list[Literal["markdown", "json", "pdf"]] = Field(
        default_factory=lambda: ["markdown", "json", "pdf"], min_length=1, max_length=3
    )
    show_coverage: bool = True
    include_context: bool = True

    @model_validator(mode="after")
    def unique_options(self):
        if len(self.fields) != len(set(self.fields)) or len(self.exports) != len(set(self.exports)):
            raise ValueError("В fields и exports не должно быть повторов")
        return self


class Example(DataModel):
    label: ShortText
    text: Annotated[str, Field(min_length=1, max_length=60000)]


class AgentLabel(DataModel):
    title: ShortText
    scope: ShortText


class ProfileTranslation(DataModel):
    title: ShortText | None = None
    report_title: ShortText | None = None
    agents: dict[Slug, AgentLabel] = Field(default_factory=dict)
    criteria: dict[CriterionID, ShortText] = Field(default_factory=dict)
    references: dict[Slug, ShortText] = Field(default_factory=dict)
    examples: list[Example] | None = Field(default=None, max_length=8)


class CheckPlan(DataModel):
    criteria: list[CriterionID] | None = Field(default=None, min_length=1, max_length=100)
    minimum_severity: Severity = "Low"
    max_findings: int | None = Field(default=None, ge=1, le=20)
    ambiguities: bool = True
    feasibility: bool = True


class AmbiguityCopy(DataModel):
    terms: list[ShortText] = Field(min_length=1, max_length=20)
    question: ShortText
    example: Instruction


class AuditProfile(DataModel):
    id: Slug
    version: Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]
    title: ShortText
    context: str = Field(default="", max_length=5000)
    guidance: str = Field(default="", max_length=5000)
    agents: dict[Slug, Agent] = Field(min_length=1, max_length=8)
    criteria: dict[CriterionID, Criterion] = Field(min_length=1, max_length=100)
    references: dict[Slug, Reference] = Field(default_factory=dict)
    scoring: ScoreRules
    report: ReportFormat
    examples: list[Example] = Field(default_factory=list, max_length=8)
    translations: dict[Literal["kk", "en"], ProfileTranslation] = Field(default_factory=dict)
    modes: dict[CheckMode, CheckPlan] = Field(default_factory=dict)
    ambiguity_dictionary: dict[Slug, dict[Language, AmbiguityCopy]] = Field(default_factory=dict, max_length=40)

    @model_validator(mode="after")
    def check_links(self):
        assigned = set()
        if "all" in self.agents:
            raise ValueError("ID агента all зарезервирован")
        for key, criterion in self.criteria.items():
            if len(criterion.agents) != len(set(criterion.agents)):
                raise ValueError(f"{key}: агент указан дважды")
            unknown_agents = set(criterion.agents) - self.agents.keys()
            unknown_refs = set(criterion.references) - self.references.keys()
            if unknown_agents or unknown_refs:
                raise ValueError(
                    f"{key}: неизвестные агенты {sorted(unknown_agents)} или источники {sorted(unknown_refs)}"
                )
            assigned.update(criterion.agents)
        if assigned != self.agents.keys():
            raise ValueError(f"У агентов нет критериев: {sorted(self.agents.keys() - assigned)}")
        for mode, plan in self.modes.items():
            if plan.criteria and (len(set(plan.criteria)) != len(plan.criteria) or set(plan.criteria) - self.criteria.keys()):
                raise ValueError(f"{mode}: критерии должны быть уникальными и существовать в профиле")
        if any(not copies for copies in self.ambiguity_dictionary.values()):
            raise ValueError("Для словаря требуется хотя бы один перевод каждой формулировки")
        for language, translation in self.translations.items():
            for section in ("agents", "criteria", "references"):
                if getattr(translation, section).keys() - getattr(self, section).keys():
                    raise ValueError(f"{language}.{section}: перевод относится к неизвестному ID")
        return self

    def criteria_for(self, agent_key: str) -> dict[str, Criterion]:
        return {key: value for key, value in self.criteria.items() if agent_key in value.agents}

    def check_plan(self, mode: CheckMode) -> CheckPlan:
        if mode in self.modes:
            return self.modes[mode]
        if mode == "quick":
            critical = [key for key, criterion in self.criteria.items() if criterion.default_severity == "High"]
            return CheckPlan(criteria=critical or None, minimum_severity="High", max_findings=4, ambiguities=False)
        return CheckPlan()

    def check_criteria(self, mode: CheckMode) -> dict[str, Criterion]:
        selection = self.check_plan(mode).criteria
        return {key: criterion for key, criterion in self.criteria.items() if selection is None or key in selection}

    def identity(self, language: Language = "ru") -> dict:
        translation = self.translations.get(language)
        return {
            "id": self.id,
            "version": self.version,
            "title": translation.title if translation and translation.title else self.title,
            "fingerprint": self.fingerprint,
        }

    def agent_label(self, key: str, language: Language) -> Agent | AgentLabel:
        translation = self.translations.get(language)
        return translation.agents.get(key, self.agents[key]) if translation else self.agents[key]

    def criterion_title(self, key: str, language: Language) -> str:
        translation = self.translations.get(language)
        return translation.criteria.get(key, self.criteria[key].title) if translation else self.criteria[key].title

    def reference_title(self, key: str, language: Language) -> str:
        translation = self.translations.get(language)
        return translation.references.get(key, self.references[key].title) if translation else self.references[key].title

    def localized_examples(self, language: Language) -> list[Example]:
        translation = self.translations.get(language)
        return translation.examples if translation and translation.examples is not None else self.examples

    def report_format(self, language: Language) -> dict:
        report = self.report.model_dump(mode="json")
        translation = self.translations.get(language)
        if translation and translation.report_title:
            report["title"] = translation.report_title
        return report


def load_profile(path: str | Path | None = None) -> AuditProfile:
    selected = Path(path or os.getenv("AUDIT_PROFILE") or DEFAULT_PROFILE)
    if not selected.is_absolute():
        selected = BASE_DIR / selected
    return read_data(selected, AuditProfile)
