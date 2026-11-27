
from pathlib import Path
from collections import Counter
from datetime import datetime
from xml.sax.saxutils import escape
import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak
)

from core.audit_metrics import audit_metrics, needs_human_review
from core.models import ValidationResult
from agents.auditor import prepare_findings


BASE_DIR = Path(__file__).resolve().parent.parent

GREEN = colors.HexColor("#123B35")
LIGHT_GREEN = colors.HexColor("#E8F3EF")
GRAY = colors.HexColor("#64748B")
LIGHT_GRAY = colors.HexColor("#F1F5F9")

SEVERITY_COLORS = {
    "critical": "#991B1B",
    "high": "#DC2626",
    "medium": "#B45309",
    "low": "#4D7C0F"
}

SEVERITY_NAMES = {
    "critical": "Критический",
    "high": "Высокий",
    "medium": "Средний",
    "low": "Низкий"
}

READINESS_NAMES = {
    "ready": "Готово",
    "conditionally_ready": "Условно готово",
    "not_ready": "Не готово к разработке"
}

DECISION_NAMES = {
    "confirmed": "Подтверждено",
    "revised": "Скорректировано",
    "rejected": "Отклонено",
    "merged": "Объединено"
}


def register_fonts():

    fonts_dir = Path(
        os.environ.get("WINDIR", r"C:\Windows")
    ) / "Fonts"

    regular = fonts_dir / "arial.ttf"
    bold = fonts_dir / "arialbd.ttf"

    if os.name != "nt" and (not regular.exists() or not bold.exists()):
        fonts_dir = Path("/usr/share/fonts/truetype/dejavu")
        regular, bold = fonts_dir / "DejaVuSans.ttf", fonts_dir / "DejaVuSans-Bold.ttf"

    if not regular.exists() or not bold.exists():
        raise FileNotFoundError(
            "Unicode fonts unavailable. Install Arial or DejaVu Sans."
        )

    pdfmetrics.registerFont(
        TTFont("ReportArial", str(regular))
    )

    pdfmetrics.registerFont(
        TTFont("ReportArialBold", str(bold))
    )

    pdfmetrics.registerFontFamily(
        "ReportArial",
        normal="ReportArial",
        bold="ReportArialBold"
    )


def generate_pdf(result, output_path, *, executive=False):

    register_fonts()

    output_path = Path(output_path)

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=42,
        leftMargin=42,
        topMargin=48,
        bottomMargin=48,
        title="AI Requirements Validation Report",
        author="AI Requirements Validator"
    )

    styles = {

        "title": ParagraphStyle(
            "ReportTitle",
            fontName="ReportArialBold",
            fontSize=26,
            leading=32,
            textColor=GREEN,
            spaceAfter=18
        ),

        "heading": ParagraphStyle(
            "ReportHeading",
            fontName="ReportArialBold",
            fontSize=16,
            leading=21,
            textColor=GREEN,
            spaceBefore=14,
            spaceAfter=10,
            keepWithNext=True
        ),

        "subheading": ParagraphStyle(
            "ReportSubheading",
            fontName="ReportArialBold",
            fontSize=12,
            leading=16,
            textColor=GREEN,
            spaceBefore=10,
            spaceAfter=6,
            keepWithNext=True
        ),

        "body": ParagraphStyle(
            "ReportBody",
            fontName="ReportArial",
            fontSize=10,
            leading=15,
            spaceAfter=7
        ),

        "small": ParagraphStyle(
            "ReportSmall",
            fontName="ReportArial",
            fontSize=8,
            leading=11,
            textColor=GRAY,
            spaceAfter=5
        ),

        "cell": ParagraphStyle(
            "ReportCell",
            fontName="ReportArial",
            fontSize=9,
            leading=13
        ),

        "metric": ParagraphStyle(
            "ReportMetric",
            fontName="ReportArialBold",
            fontSize=23,
            leading=28,
            alignment=TA_CENTER,
            textColor=GREEN
        ),

        "metric_label": ParagraphStyle(
            "MetricLabel",
            fontName="ReportArial",
            fontSize=9,
            leading=12,
            alignment=TA_CENTER,
            textColor=GRAY
        )
    }

    story = []

    def paragraph(text, style="body"):
        return Paragraph(
            escape(str(text)).replace("\n", "<br/>"),
            styles[style]
        )

    def add(text, style="body"):
        story.append(paragraph(text, style))

    def field(label, value):

        story.append(
            Paragraph(
                f"<b>{escape(str(label))}:</b> "
                f"{escape(str(value))}",
                styles["body"]
            )
        )

    def table(rows, widths):

        converted = [
            [paragraph(cell, "cell") for cell in row]
            for row in rows
        ]

        t = Table(
            converted,
            colWidths=widths,
            repeatRows=1,
            hAlign="LEFT"
        )

        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), LIGHT_GREEN),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.white, LIGHT_GRAY]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("LINEBELOW", (0, 0), (-1, 0), 1, GREEN)
        ]))

        story.append(t)
        story.append(Spacer(1, 12))

    retained = [
        d for d in result.audit.decisions
        if d.decision in ("confirmed", "revised")
    ]

    metrics_data = audit_metrics(result)
    severity_counts = {key.lower(): value for key, value in metrics_data["counts"].items()}

    decision_counts = Counter(
        d.decision for d in result.audit.decisions
    )

    requirements = {
        r.id: r
        for r in result.extraction.requirements
    }

    original_findings = {
        f["id"]: f
        for f in prepare_findings(
            result.quality,
            result.consistency
        )
    }

    # SECTION 1: EXECUTIVE DASHBOARD

    add("AI REQUIREMENTS VALIDATOR", "small")

    add(
        "Краткий отчёт для руководителя" if executive else "Отчёт о качестве технического задания",
        "title"
    )

    add(
        "Автоматизированная мультиагентная проверка",
        "body"
    )

    field(
        "Документы",
        ", ".join(result.source_files)
    )

    field(
        "Дата формирования",
        datetime.now().strftime("%d.%m.%Y %H:%M")
    )

    field(
        "Готовность",
        READINESS_NAMES[result.audit.readiness]
    )

    metrics = Table(
        [
            [
                paragraph(len(requirements), "metric"),
                paragraph(metrics_data["unique_findings"], "metric"),
                paragraph("4", "metric")
            ],
            [
                paragraph("Требований", "metric_label"),
                paragraph("Замечаний", "metric_label"),
                paragraph("AI-агента", "metric_label")
            ]
        ],
        colWidths=[170, 170, 170]
    )

    metrics.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT_GREEN),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE")
    ]))

    story.append(metrics)

    add("1. Распределение замечаний", "heading")

    rows = [["Серьёзность", "Количество"]]

    for severity in (
        "critical", "high", "medium", "low"
    ):
        rows.append([
            SEVERITY_NAMES[severity],
            severity_counts[severity]
        ])

    table(rows, [350, 160])

    add("2. Паспорт проверки", "heading")

    field(
        "Время анализа",
        f"{result.execution_time_seconds:.2f} сек."
    )

    field(
        "Оценка качества требований до аудита",
        f'{metrics_data["score"]}/100' if metrics_data["score"] is not None else "—"
    )

    field(
        "Объединено замечаний",
        decision_counts["merged"]
    )

    field(
        "Отклонено замечаний",
        decision_counts["rejected"]
    )

    add(
        "Оценка качества является экспертной "
        "оценкой AI, а не сертифицированным "
        "показателем соответствия.",
        "small"
    )

    field("Кандидаты для ручной проверки", metrics_data["human_review"])
    if executive:
        passport = result.passport
        field("Настроенная модель", passport.get("configured_model", "—"))
        field("Запуск / режим", f"{passport.get('run_kind', '—')} / {passport.get('mode', '—')}")
        story.append(PageBreak())
        add("Основные риски", "heading")
        rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        risks = sorted((d for d in retained if not needs_human_review(d)),
                       key=lambda d: (rank[d.final_severity], d.finding_id))
        for d in risks[:5]:
            field(d.finding_id, f"{d.title[:140]} ({', '.join(d.requirement_ids[:6])}). {d.explanation[:180]}")
        add("Приоритетные решения", "heading")
        for action in result.audit.priority_actions[:5]:
            add(action[:300])
        add("Ограничения анализа", "heading")
        add("Аудит охватывает извлечённые требования. Полнота и оценка ИИ требуют экспертной проверки.")
        for limitation in result.audit.limitations[:3]:
            add(limitation[:240])
        doc.build(story)
        return

    # SECTION 2: SUMMARY AND ACTIONS

    story.append(PageBreak())

    add("3. Итоговый вывод", "heading")

    add(
        f"По итогам аудита сохранено "
        f'{metrics_data["unique_findings"]} замечаний'
        f", включая "
        f"{severity_counts['critical']} критических "
        f"и {severity_counts['high']} высоких."
    )

    add(
        "Статус готовности: "
        + READINESS_NAMES[result.audit.readiness]
    )

    add("4. Приоритетные действия", "heading")

    for number, action in enumerate(
        result.audit.priority_actions,
        start=1
    ):
        add(f"{number}. {action}")

    add("5. Проверка полноты извлечения", "heading")

    field(
        "Статус",
        result.audit.extraction_audit.status
    )

    add(result.audit.extraction_audit.comments)

    for item in (
        result.audit.extraction_audit.missing_requirements
    ):
        field("Возможный пропуск", item)

    for item in (
        result.audit.extraction_audit.unsupported_requirements
    ):
        field("Неподтверждённое требование", item)

    # SECTION 3: DETAILED FINDINGS

    story.append(PageBreak())

    add("6. Детальный реестр замечаний", "heading")

    severity_order = {
        "critical": 0,
        "high": 1,
        "medium": 2,
        "low": 3
    }

    retained.sort(
        key=lambda d: severity_order[
            d.final_severity
        ]
    )

    for decision in retained:

        original = original_findings[
            decision.finding_id
        ]

        add(
            f"{decision.finding_id} — {decision.title}",
            "subheading"
        )

        severity = decision.final_severity

        story.append(
            Paragraph(
                "<b>Серьёзность:</b> "
                f'<font color="{SEVERITY_COLORS[severity]}">'
                f"{SEVERITY_NAMES[severity]}</font>",
                styles["body"]
            )
        )

        field(
            "Решение аудитора",
            DECISION_NAMES[decision.decision]
        )

        field(
            "Связанные требования",
            ", ".join(decision.requirement_ids)
        )

        evidence = original.get("evidence")

        if isinstance(evidence, str):

            requirement = requirements.get(
                original.get("requirement_id")
            )

            if requirement:

                normalized_quote = " ".join(
                    evidence.split()
                )

                normalized_text = " ".join(
                    requirement.text.split()
                )

                if (
                    normalized_quote
                    and normalized_quote in normalized_text
                ):
                    field("Цитата", evidence)

                else:
                    add(
                        "Цитата AI не прошла "
                        "программную проверку.",
                        "small"
                    )

        elif isinstance(evidence, list):

            for item in evidence:
                field(
                    f"Цитата {item['requirement_id']}",
                    item["quote"]
                )

        for requirement_id in decision.requirement_ids:

            requirement = requirements.get(
                requirement_id
            )

            if requirement:

                location = requirement.source

                if requirement.page is not None:
                    location += (
                        f", стр. {requirement.page}"
                    )

                if requirement.paragraph is not None:
                    location += (
                        f", абз. {requirement.paragraph}"
                    )

                field(
                    f"Источник {requirement_id}",
                    location
                )

        field("Проблема", decision.explanation)

        field(
            "Рекомендация",
            decision.recommendation
        )

        field(
            "Обоснование аудитора",
            ("Требует ручной проверки. " if needs_human_review(decision) else "") + decision.audit_reason
        )

        field(
            "Некалиброванная оценка ИИ",
            f"{decision.confidence * 100:.0f}%"
        )

        story.append(Spacer(1, 12))

    # SECTION 4: REQUIREMENTS REGISTER

    story.append(PageBreak())

    add("7. Реестр требований", "heading")

    rows = [
        ["ID", "Требование", "Источник"]
    ]

    for requirement in (
        result.extraction.requirements
    ):

        location = requirement.source

        if requirement.page is not None:
            location += f"\nСтр. {requirement.page}"

        if requirement.paragraph is not None:
            location += (
                f"\nАбз. {requirement.paragraph}"
            )

        rows.append([
            requirement.id,
            requirement.text,
            location
        ])

    table(rows, [75, 330, 105])

    # SECTION 5: AUDIT TRAIL

    add("8. Журнал аудита", "heading")

    rows = [
        ["ID", "Решение", "Объединено с"]
    ]

    for decision in result.audit.decisions:

        rows.append([
            decision.finding_id,
            DECISION_NAMES[decision.decision],
            decision.merged_into or "-"
        ])

    table(rows, [110, 250, 150])

    add("9. Ограничения анализа", "heading")

    for limitation in result.audit.limitations:
        add("- " + limitation)

    add(
        "- AI может пропускать дефекты "
        "или формировать ошибочные выводы."
    )

    add(
        "- Confidence является оценкой модели, "
        "а не статистической вероятностью."
    )

    add(
        "- Банковские, технические и регуляторные "
        "решения требуют подтверждения "
        "ответственными специалистами."
    )

    # HEADER AND FOOTER

    def decorate_page(canvas, document):

        canvas.saveState()

        width, height = A4

        canvas.setFillColor(GREEN)

        canvas.rect(
            0, height - 8,
            width, 8,
            fill=1,
            stroke=0
        )

        canvas.setFont("ReportArial", 8)
        canvas.setFillColor(GRAY)

        canvas.drawString(
            42,
            height - 29,
            "AI REQUIREMENTS VALIDATOR"
        )

        canvas.setStrokeColor(LIGHT_GRAY)

        canvas.line(
            42, 37,
            width - 42, 37
        )

        canvas.drawString(
            42, 24,
            "Автоматизированный аналитический отчёт"
        )

        canvas.drawRightString(
            width - 42,
            24,
            f"Страница {document.page}"
        )

        canvas.restoreState()

    doc.build(
        story,
        onFirstPage=decorate_page,
        onLaterPages=decorate_page
    )

    print(f"[PDF] Report generated: {output_path}")

    return output_path


if __name__ == "__main__":

    input_path = (
        BASE_DIR / "reports" /
        "final_validation.json"
    )

    result = ValidationResult.model_validate_json(
        input_path.read_text(encoding="utf-8")
    )

    generate_pdf(
        result,
        BASE_DIR / "reports" /
        "final_report.pdf"
    )
