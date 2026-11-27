from typing import Literal

from pydantic import BaseModel, Field, field_validator

Severity = Literal["Critical", "High", "Medium", "Low"]
SEVERITIES = ("Critical", "High", "Medium", "Low")
CheckMode = Literal["quick", "deep"]
DocumentRole = Literal["main", "appendix", "glossary", "related", "checklist"]
DOCUMENT_ROLES = ("main", "appendix", "glossary", "related", "checklist")
FILE_ID_PATTERN = r"^D[1-5]$"
MISSING_QUOTE = "Отсутствует в тексте ТЗ"

# Синонимы, которые иногда возвращают модели без строгого Structured Outputs
# (JSON-режим / свободный текст у сторонних провайдеров).
_SEVERITY_ALIASES = {
    "high": "High",
    "critical": "Critical",
    "blocker": "High",
    "высокий": "High",
    "высокая": "High",
    "критический": "High",
    "критичный": "High",
    "medium": "Medium",
    "moderate": "Medium",
    "major": "Medium",
    "средний": "Medium",
    "средняя": "Medium",
    "low": "Low",
    "minor": "Low",
    "info": "Low",
    "низкий": "Low",
    "низкая": "Low",
}


class Defect(BaseModel):
    severity: Severity = Field(
        description="Критичность риска: High (критический блокер/уязвимость), Medium (существенный пробел), Low (уточнение формулировки)"
    )
    criterion_id: str = Field(default="", max_length=80, description="Один из ID критериев этого агента")
    title: str = Field(min_length=1, max_length=200, description="Короткое понятное название проблемы")
    quote: str = Field(
        max_length=600,
        description=f"Дословный фрагмент ТЗ (или '{MISSING_QUOTE}', если требование пропущено)",
    )
    related_quotes: list[str] = Field(
        default_factory=list, max_length=2, description="Другие дословные фрагменты для этой находки"
    )
    file_id: str = Field(default="", pattern=r"^(D[1-5])?$", description="ID файла основной цитаты")
    related_file_ids: list[str] = Field(default_factory=list, max_length=2, description="ID файлов в порядке related_quotes")
    issue: str = Field(
        min_length=1, max_length=2200, description="Почему это важно и к чему приведёт"
    )
    suggestion: str = Field(
        min_length=1, max_length=2200, description="Проверяемая формулировка для вставки в ТЗ"
    )
    fix_kind: Literal["rewrite", "clarify"] = Field(
        default="clarify",
        description="rewrite только для прямой замены одной цитаты без неизвестных параметров",
    )
    clarification: str = Field(
        default="", max_length=500, description="Вопрос заказчику для clarify, иначе пустая строка"
    )

    @field_validator("severity", mode="before")
    @classmethod
    def _normalize_severity(cls, value):
        if isinstance(value, str):
            return _SEVERITY_ALIASES.get(value.strip().lower(), value.strip())
        return value

    @field_validator("criterion_id", "title", "quote", "issue", "suggestion", "clarification", mode="before")
    @classmethod
    def _coerce_text(cls, value):
        if value is None:
            return ""
        if isinstance(value, (list, tuple)):
            return "\n".join(str(v) for v in value)
        return value.strip() if isinstance(value, str) else value

    @field_validator("related_quotes")
    @classmethod
    def _check_related_quotes(cls, quotes: list[str]) -> list[str]:
        if any(not quote.strip() or len(quote) > 600 for quote in quotes):
            raise ValueError("В related_quotes нужны непустые цитаты до 600 символов")
        return [quote.strip() for quote in quotes]

    @field_validator("related_file_ids")
    @classmethod
    def _check_related_ids(cls, ids: list[str]) -> list[str]:
        if any(value not in ("", "D1", "D2", "D3", "D4", "D5") for value in ids):
            raise ValueError("Ожидается ID файла D1–D5")
        return ids


class AuditReport(BaseModel):
    defects: list[Defect]
