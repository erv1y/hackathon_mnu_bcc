"""Запуск REQUIRAGENTS из консоли и формирование русского отчёта для сдачи."""

import argparse
import asyncio
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from analyzer import ConfigError, _get_client, describe_llm_error, get_settings, request_model_json
from audit_documents import DocumentBundle, DocumentSource, MAX_DOCUMENTS
from audit_profile import BASE_DIR, DataFileError, load_profile
from parser import ParseError, parse_document
from quality_audit import RUBRIC_PATH, QualityRubric, load_quality_rubric, review_quality
from review_insights import build_insights
from schemas import DOCUMENT_ROLES

AGENT_VERSION = "v1.3.0"
STATUS_LABEL = {"pass": "Пройдено", "fail": "Нарушение", "na": "Неприменимо", "unknown": "Нет подтверждённой оценки"}
SEVERITY_LABEL = {"High": "Критично", "Medium": "Важно", "Low": "Уточнить"}
_ORDER = {severity: index for index, severity in enumerate(SEVERITY_LABEL)}


def _plain(value: str) -> str:
    value = " ".join(str(value).split())
    value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_{}\[\]()#+.!|])", r"\\\1", value)


def _violations(checks: dict):
    for criterion in checks.values():
        for item in criterion["findings"]:
            yield item


def _place(evidence: dict) -> str:
    start = evidence.get("file_line_start", evidence["line_start"])
    end = evidence.get("file_line_end", evidence["line_end"])
    place = f"строка {start}" if start == end else f"строки {start}–{end}"
    name = evidence.get("file_name")
    return f"{_plain(name)} · {place}" if name else place


def _findings_report(lines: list[str], checks: dict) -> None:
    lines.extend(["| Критерий | Результат |", "| --- | --- |"])
    for check in checks.values():
        lines.append(f"| {_plain(check['label'])} | {STATUS_LABEL[check['status']]} |")
    lines.append("")
    for check in checks.values():
        for item in check["findings"]:
            lines.append(f"- **{SEVERITY_LABEL[item['severity']]} · {_plain(item['title'])}.** {_plain(item['issue'])}")
            for evidence in item["evidence"]:
                lines.append(f"  - {_place(evidence)} · «{_plain(evidence['text'])}»")
            lines.append(f"  - Исправление или уточнение — {_plain(item['suggestion'])}")
    lines.append("")


def _insights_report(lines: list[str], insights: dict) -> None:
    lines.extend(["", "## Сводка по ролям", ""])
    for key, role in [("lead", "Руководитель"), ("developer", "Разработчик"), ("tester", "Тестировщик")]:
        summary = insights["summaries"][key]
        lines.extend([f"### {role}", "", _plain(summary["text"]), ""])
        lines.extend(f"- {_plain(item)}" for item in summary["items"])
        lines.append("")
    for key, title in [("ambiguities", "Расплывчатые места"), ("sanity", "Выполнимость правил"),
                       ("questions", "Вопросы заказчику"), ("scenarios", "Защитные сценарии")]:
        if not insights[key]:
            continue
        lines.extend([f"## {title}", ""])
        for row in insights[key]:
            heading = row.get("question") if key == "questions" else row["title"]
            lines.append(f"- **{_plain(heading)}**")
            for proof in row["evidence"]:
                lines.append(f"  - {_place(proof)} · «{_plain(proof['text'])}»")
            if key == "scenarios":
                for part, label in [("condition", "Условие"), ("check", "Проверка"), ("expected", "Ожидаемый итог")]:
                    lines.append(f"  - {label} — {_plain(row[part])}")
            else:
                label = "Пример для согласования" if key == "ambiguities" or row.get("example") else "Вариант для согласования"
                lines.append(f"  - {label} — {_plain(row['default'])}")
        lines.append("")


def make_submission_report(
    review: dict, *, document_name: str, file_name: str, model: str, agent_version: str,
    source_warnings: tuple[str, ...] = (), generated_at: datetime | None = None,
) -> tuple[str, bool]:
    rows = review["requirements"]
    scopes = [item["checks"] for item in rows] + [review["whole"]]
    unknown = sum(check["status"] == "unknown" for scope in scopes for check in scope.values())
    complete = not (unknown or source_warnings or review["failed_calls"])
    found = [
        (item["id"], finding) for item in rows for finding in _violations(item["checks"])
    ] + [("Набор целиком", finding) for finding in _violations(review["whole"])]
    ranked = sorted(found, key=lambda pair: _ORDER[pair[1]["severity"]])
    critical = [(scope, item) for scope, item in ranked if item["severity"] == "High"]
    timestamp = (generated_at or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    mode = review.get("mode", "deep")
    ready = (
        "Проверка неполная. Готовность к разработке и тестированию не установлена."
        if not complete else
        "Быстрая проверка завершена. Для решения о готовности проведите глубокую проверку."
        if mode == "quick" else
        "Перед началом разработки и тестирования закройте критичные и важные нарушения."
        if any(item["severity"] in {"High", "Medium"} for _, item in ranked) else
        "По проверенным критериям можно начинать разработку и тестирование."
    )
    lines = [
        "# REQUIRAGENTS · Отчёт о проверке требований", "",
        "## Паспорт прогона", "",
        f"- Документ — {_plain(document_name)}",
        f"- Входной файл — {_plain(file_name)}",
        f"- Режим — {'быстрый' if mode == 'quick' else 'глубокий'}",
        f"- Дата и время — {_plain(timestamp)}",
        f"- Модель — {_plain(model)}",
        f"- Версия агента — {_plain(agent_version)}",
        f"- Набор критериев (SHA-256) — `{review['rubric_sha256']}`",
        "- Язык — русский",
        "",
        "## Сводка", "",
        f"- Требований — {len(rows)}",
        f"- Нарушений — {len(found)} по отдельным требованиям и набору в сумме",
        f"- Критичных нарушений — {len(critical)}",
        f"- Оценок без подтверждения — {unknown}",
        f"- Запросов к модели с ошибкой — {review['failed_calls']}",
        "",
        "### Критичные нарушения", "",
    ]
    for scope, item in critical:
        places = ", ".join(_place(proof) for proof in item["evidence"])
        lines.append(f"- {_plain(scope)} · {_plain(item['title'])} · {places}")
    if not critical:
        lines.append("Критичных нарушений среди полученных оценок нет.")
    for notice in source_warnings:
        lines.append(f"- {_plain(notice)}")
    lines.extend([
        "", "## Выделенные требования", "",
        "| ID в отчёте | ID в документе | Строки извлечённого текста | Краткая формулировка |",
        "| --- | --- | --- | --- |",
    ])
    for item in rows:
        excerpt = " ".join(item["text"].split())
        short = excerpt[:150] + "…" if len(excerpt) > 150 else excerpt
        location = _place(item) if item.get("file_name") else f"{item['line_start']}–{item['line_end']}"
        lines.append(
            f"| {item['id']} | {_plain(item['source_id'] or '—')} | "
            f"{location} | {_plain(short)} |"
        )
    lines.extend(["", "## Проверка каждого требования", ""])
    for item in rows:
        section = f" · {_plain(item['section'])}" if item["section"] else ""
        lines.extend([f"### {item['id']}{section}", ""])
        if item["table_columns"]:
            lines.append(f"Поля таблицы — {_plain(item['table_columns'])}")
        lines.extend([f"> {_plain(item['text'])}", ""])
        _findings_report(lines, item["checks"])
    lines.extend(["## Проверка набора целиком", ""])
    _findings_report(lines, review["whole"])
    lines.extend(["## Итог", "", ready, ""])
    if ranked:
        lines.extend(["### Что исправить первым", ""])
        for scope, item in ranked[:5]:
            lines.append(f"- {_plain(scope)} · {_plain(item['title'])} — {_plain(item['suggestion'])}")
    if review.get("insights"):
        _insights_report(lines, review["insights"])
    return "\n".join(lines).rstrip() + "\n", complete


async def run_quality_audit(source: str | DocumentBundle, settings, rubric: QualityRubric) -> dict:
    async with _get_client(settings) as client:
        async def ask(system, payload):
            return await request_model_json(client, settings, system, payload)

        return await review_quality(source, rubric, ask, parallel=settings.max_parallel)


def _attachment(value: str) -> tuple[str, Path]:
    role, sep, path = value.partition("=")
    if not sep or role not in DOCUMENT_ROLES or not path:
        raise argparse.ArgumentTypeError("формат ROLE=PATH, роли: " + ", ".join(DOCUMENT_ROLES))
    return role, Path(path)


def _read_bundle(entries: list[tuple[str, Path]]) -> DocumentBundle:
    sources = []
    size = 0
    for role, path in entries:
        with path.open("rb") as stream:
            raw = stream.read(10 * 1024 * 1024 + 1)
        if len(raw) > 10 * 1024 * 1024:
            raise ValueError(f"{path.name} больше 10 МБ.")
        parsed = parse_document(path.name, raw, max_chars=60000)
        size += len(parsed.text) + (2 if sources else 0)
        if size > 60000:
            raise ValueError("В пакете больше 60 000 символов.")
        sources.append(DocumentSource(parsed.text, path.name, role, parsed.warnings))
    return DocumentBundle(sources)


def _summary_findings(review: dict) -> list[dict]:
    findings = []
    for checks in [*(row["checks"] for row in review["requirements"]), review["whole"]]:
        for key, check in checks.items():
            for finding in check["findings"]:
                if review["mode"] == "quick" and finding["severity"] != "High":
                    continue
                findings.append({**finding, "criterion_id": key, "id": f"F-{len(findings) + 1:03d}"})
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="документ PDF, DOCX, MD или TXT")
    parser.add_argument("output", type=Path, help="путь к русскому отчёту .md")
    parser.add_argument("--agent-version", default=AGENT_VERSION, help="тег выпуска или коммит агента")
    parser.add_argument("--rubric", type=Path, default=RUBRIC_PATH, help="JSON с критериями требований и документа")
    parser.add_argument("--mode", choices=("quick", "deep"), default="deep", help="быстрая или глубокая проверка")
    parser.add_argument("--file", type=_attachment, action="append", default=[], metavar="ROLE=PATH",
                        help="добавить документ с указанной ролью, до четырёх дополнений")
    args = parser.parse_args(argv)
    if args.output.suffix.lower() != ".md":
        parser.error("имя отчёта должно заканчиваться на .md")
    entries = [("main", args.input), *args.file]
    if len(entries) > MAX_DOCUMENTS:
        parser.error("в пакете может быть до пяти файлов")
    if any(path.resolve() == args.output.resolve() for _, path in entries):
        parser.error("укажите отдельный выходной файл")
    try:
        bundle = _read_bundle(entries)
        if os.getenv("AUDIT_LOAD_DOTENV", "1") != "0":
            load_dotenv(BASE_DIR / ".env")
        settings = get_settings()
        rubric = load_quality_rubric(args.rubric).for_mode(args.mode)
        source = bundle if len(entries) > 1 else bundle.text
        review = asyncio.run(run_quality_audit(source, settings, rubric))
        profile = getattr(settings, "profile", None) or load_profile()
        review["insights"] = build_insights(bundle, _summary_findings(review), profile, args.mode, "ru")
        header = re.match(r"^#\s+([^\n]+)", bundle.sources[0].text)
        report, complete = make_submission_report(
            review, document_name=header.group(1) if header else args.input.stem,
            file_name=", ".join(item["name"] for item in bundle.manifest),
            model=settings.model, agent_version=args.agent_version, source_warnings=bundle.warnings,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
    except (OSError, ParseError, ConfigError, DataFileError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        _, message = describe_llm_error(exc)
        print(message, file=sys.stderr)
        return 2

    print(f"Отчёт записан в {args.output.resolve()}")
    if not complete:
        print("Проверка неполная. Устраните причины и повторите прогон.", file=sys.stderr)
    return 0 if complete else 1


if __name__ == "__main__":
    sys.exit(main())
