"""Printable, explicitly labeled reports from the built-in offline example."""

from html import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from core.report_generator import GREEN, draw_report_frame, register_fonts
from product_review import group_findings
from schemas import SEVERITIES


COPY = {
    "ru": ("Краткий отчёт", "Технический отчёт", "Сохранённый демонстрационный пример",
           "Готовность не оценена. Показаны результаты примера.", "Ключевые риски и решения",
           "Доказательства", "Предложение для согласования", "Реестр требований", "Сценарии проверки",
           "Замечания"),
    "en": ("Executive report", "Technical report", "Saved demonstration replay",
           "Readiness unassessed. Findings come from a saved example.", "Key risks and decisions",
           "Evidence", "Proposal requiring approval", "Requirements register", "Test scenarios",
           "Findings"),
    "kk": ("Қысқаша есеп", "Техникалық есеп", "Сақталған демонстрациялық мысал",
           "Дайындық бағаланбады. Нәтижелер сақталған мысалдан алынды.", "Негізгі тәуекелдер мен шешімдер",
           "Дәлелдер", "Мақұлдауды қажет ететін ұсыныс", "Талаптар тізілімі", "Сынақ сценарийлері",
           "Ескертулер"),
}
LEVELS = {
    "ru": ("Критический", "Высокий", "Средний", "Низкий"),
    "en": SEVERITIES,
    "kk": ("Сындарлы", "Жоғары", "Орташа", "Төмен"),
}


def render_replay_pdf(report, title, target, *, executive=False):
    register_fonts()
    language = report["language"]
    short, long, replay, limitation, risks, evidence, proposal, register, scenarios, findings = COPY[language]
    names = dict(zip(SEVERITIES, LEVELS[language], strict=True))
    doc = SimpleDocTemplate(str(target), pagesize=A4, leftMargin=44, rightMargin=44,
                            topMargin=55, bottomMargin=48, title=f"REQUIRAGENTS · {title}")
    body = ParagraphStyle("ReplayBody", fontName="ReportArial", fontSize=10, leading=14,
                          textColor=colors.HexColor("#192822"), spaceAfter=6, splitLongWords=1)
    heading = ParagraphStyle("ReplayHeading", parent=body, fontName="ReportArialBold",
                             fontSize=13, leading=18, textColor=GREEN, spaceBefore=12, keepWithNext=True)
    hero = ParagraphStyle("ReplayTitle", parent=heading, fontSize=25, leading=29)
    cite = ParagraphStyle("ReplayEvidence", parent=body, backColor=colors.HexColor("#eef6f0"),
                          borderColor=colors.HexColor("#c8ded0"), borderWidth=.6, borderPadding=6,
                          leftIndent=7, rightIndent=7, spaceAfter=11)

    def paragraph(text, style=body):
        return Paragraph(escape(str(text)).replace("\n", "<br/>"), style)

    rows = [row for module in report["modules"] for row in module["defects"] if not row.get("review_reason")]
    groups = group_findings(rows)
    selected = sorted((group[0] for group in groups), key=lambda row: SEVERITIES.index(row["severity"]))
    if executive:
        selected = [row for row in selected if row["severity"] == "Critical"] + [
            row for row in selected if row["severity"] == "High"
        ][:3]
        if not selected:
            selected = [group[0] for group in groups][:2]
    story = [paragraph(short if executive else long, hero), paragraph(title, heading),
             paragraph(replay), paragraph(limitation), Spacer(1, 8)]
    metrics = Table([[paragraph(names[level], body) for level in SEVERITIES],
                     [paragraph(report["counts"].get(level, 0), heading) for level in SEVERITIES]],
                    colWidths=[120] * 4)
    metrics.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eff6f1")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.extend([metrics, paragraph(risks if executive else findings, heading)])
    for row in selected:
        story.append(paragraph(f"{row['id']} · {row['title']} · {names[row['severity']]}", heading))
        story.append(paragraph(row["issue"]))
        for proof in row["evidence"]:
            location = proof.get("file_name") or title
            story.append(paragraph(f"{evidence} · {location} · {proof.get('line_start', '—')}\n{proof['text']}", cite))
        story.append(paragraph(f"{proposal} · {row['suggestion']}"))
    if not executive:
        story.append(paragraph(register, heading))
        source = report["document"]["text"]
        for row in report["document"]["requirements"]:
            story.append(paragraph(f"{row['id']} · {source[row['start']:row['end']]}"))
        story.append(paragraph(scenarios, heading))
        for row in report["insights"]["scenarios"]:
            story.append(paragraph(f"{row['id']} · {row['title']}", heading))
            for field in ("condition", "check", "expected"):
                story.append(paragraph(row[field]))

    def frame(canvas, document):
        draw_report_frame(canvas, document.page, language, footer=replay)

    doc.build(story, onFirstPage=frame, onLaterPages=frame)
