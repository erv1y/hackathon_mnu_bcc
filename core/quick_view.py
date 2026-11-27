"""Preliminary reports: deliberately no AuditResult or invented audit decisions."""
from pathlib import Path
from html import escape
import os
from collections import Counter
from core.pdf_labels import pdf_label


def generate_quick_pdf(result, output_path, *, executive=False):
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib import colors
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    font = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'arial.ttf'
    if not font.exists():
        font = Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    pdfmetrics.registerFont(TTFont('QuickUnicode', str(font)))
    body = ParagraphStyle('QuickBody', fontName='QuickUnicode', fontSize=10, leading=15, spaceAfter=6, wordWrap='CJK')
    heading = ParagraphStyle('QuickHeading', parent=body, fontSize=14, leading=19, textColor=colors.HexColor('#16836c'), spaceBefore=12)
    story = []
    def add(text, style=body):
        story.append(Paragraph(escape(str(text)).replace('\n', '<br/>'), style))
    language = getattr(result, "passport", {}).get("language", "ru")
    def label(value):
        return pdf_label(value, language)

    partial = getattr(result, 'auditor_failure', None)
    if partial:
        add(label('Deep Validation — частичный отчёт'), heading)
        reason = ('Проверка решений аудитора не пройдена.' if partial == 'auditor_validation_failed'
                  else 'Аудитор не получил пригодного ответа от Gemini.')
        if partial == "consistency_incomplete":
            reason = "Проверка согласованности завершена частично."
        if partial == "source_coverage_incomplete":
            reason = "Часть исходного документа не прочитана."
        add(label(reason) + ' ' + label('Независимый аудит не завершён. Готовность не оценена.'))
    else:
        add(label('Quick Validation — предварительный отчёт'), heading)
        add(label('Один AI-агент. Независимый аудит не выполнялся. Результаты требуют проверки специалистом.'))
    add(label("Документы") + ": " + ", ".join(result.source_files))
    finding_audit = getattr(result, "finding_audit", None)
    if finding_audit is not None:
        add(label("Независимо проверенные замечания при неполном покрытии"), heading)
        if result.consistency.unresolved:
            add(label("Незавершённые этапы проверки согласованности") + ": " + ", ".join(
                sorted({entry["phase"] for entry in result.consistency.unresolved})))
        for warning in getattr(result, "passport", {}).get("source_warnings", []):
            add(warning)
        critical = sum(decision.decision in ("confirmed", "revised") and decision.final_severity == "critical"
                       and decision.assessment_kind == "defect" and not decision.finding_id.startswith("REVIEW-")
                       for decision in finding_audit.decisions)
        add(f"{label('Критические замечания')} · {critical}")
        if counts := getattr(result, "passport", {}).get("product_counts"):
            add(f"{label('Исходные замечания')} · {counts['raw']}")
            add(f"{label('Группы замечаний')} · {counts['grouped']}")
        for decision in finding_audit.decisions:
            add(f"{decision.finding_id} | {decision.decision} | {decision.title}")
            if decision.merged_into:
                add(f"{label('Объединено с')} {decision.merged_into}")
            if decision.decision in ("confirmed", "revised"):
                add(decision.explanation)
                add(decision.recommendation)
                if not executive:
                    for item in decision.final_evidence:
                        add(f"{item.requirement_id} | {item.source} | {item.page or '—'} / {item.paragraph or '—'}")
                        add(item.quote)
        if not executive:
            add(label("Реестр требований"), heading)
            for requirement in result.extraction.requirements:
                add(f"{requirement.id} | {requirement.source}")
                add(requirement.text)
        SimpleDocTemplate(str(path), pagesize=A4, title="Deep Validation - Partial").build(story)
        return path
    add(f'Время текущего запуска: {result.execution_time_seconds} сек.')
    add(f'Требований: {len(result.extraction.requirements)}; замечаний качества: {sum(len(a.findings) for a in result.quality.assessments)}; замечаний согласованности: {len(result.consistency.findings)}. Эти категории могут пересекаться.')
    add(label('Реестр требований'), heading)
    for r in result.extraction.requirements:
        add(f'{r.id} | {r.source} | стр. {r.page or "—"}, абз. {r.paragraph or "—"}')
        add(r.text)
    add(label('Предварительные замечания качества'), heading)
    for a in result.quality.assessments:
        add(f'{a.requirement_id}: {a.status}, {a.quality_score}/100')
        for f in a.findings:
            add(f'{f.severity} / {f.criterion}: {f.explanation}')
            add(f'Цитата агента: {f.evidence}')
            add(f'Рекомендация: {f.recommendation}')
    add(label('Предварительные замечания согласованности'), heading)
    for f in result.consistency.findings:
        add(f'{f.id} | {f.severity} | {f.title}')
        add(f.explanation)
        for e in f.evidence:
            add(f'{e.requirement_id} | {e.source} | стр. {e.page or "—"}, абз. {e.paragraph or "—"}: {e.quote}')
        add(f'Рекомендация: {f.recommendation}')
    SimpleDocTemplate(str(path), pagesize=A4, title='Deep Validation - Partial' if partial else 'Quick Validation - Preliminary').build(story)
    return path


def preliminary_graph(result, categories, severities, connected):
    import networkx as nx
    from core.knowledge_graph import TYPES, RELATIONS, PAIR_CATEGORIES
    graph = nx.MultiGraph()
    for r in result.extraction.requirements:
        label, color = TYPES.get(r.type, TYPES['other'])
        graph.add_node('req:'+r.id, kind='requirement', label=r.id, color=color,
                       type=label, text=r.text, source=r.source, page=r.page, paragraph=r.paragraph)
    for f in result.consistency.findings:
        if f.category not in categories or f.severity not in severities:
            continue
        ids = list(dict.fromkeys(f.requirement_ids))
        if not ids or any('req:'+rid not in graph for rid in ids):
            continue
        label, color = RELATIONS[f.category]
        attrs = dict(finding_id=f.id, category=f.category, label=label, color=color,
                     severity=f.severity, title=f.title, explanation=f.explanation,
                     recommendation=f.recommendation, evidence=[e.model_dump(mode='json') for e in f.evidence],
                     review_status='preliminary')
        if len(ids)==2 and f.category in PAIR_CATEGORIES:
            graph.add_edge('req:'+ids[0], 'req:'+ids[1], **attrs)
        else:
            node = 'issue:'+f.id
            graph.add_node(node, kind='finding', **attrs)
            for rid in ids:
                graph.add_edge(node, 'req:'+rid, **attrs)
    if connected:
        graph.remove_nodes_from(list(nx.isolates(graph)))
    return graph


def show_quick_result(result, pdf_bytes, events):
    import streamlit as st
    import streamlit.components.v1 as components
    from core.knowledge_graph import RELATIONS, render_graph_html, graph_json
    st.header('2. Quick Validation — предварительный результат')
    st.warning('Независимый аудит не выполнялся. Готовность к разработке не оценена отдельным аудитором.')
    if 'Cache Hit' in events:
        st.success('Cache Hit — без новых AI-запросов.')
    elif 'Resume' in events:
        st.info('Resume — восстановление сохранённого состояния.')
    st.caption('Документы: '+', '.join(result.source_files))
    for col, label, value in zip(st.columns(4), ['Требований','Замечаний качества','Замечаний согласованности','Время запуска'], [len(result.extraction.requirements), sum(len(a.findings) for a in result.quality.assessments),len(result.consistency.findings), f'{result.execution_time_seconds:.2f} сек.']):
        col.metric(label,value)
    st.caption('Замечания качества и согласованности могут описывать один дефект.')
    if pdf_bytes:
        st.download_button('Скачать предварительный PDF',pdf_bytes,'quick_preliminary_report.pdf','application/pdf')
    st.download_button('Скачать Quick JSON',result.model_dump_json(indent=2),'quick_validation.json','application/json')
    quality, consistency, register, graph_tab = st.tabs(['Quality','Consistency','Requirements','Knowledge Graph'])
    with quality:
        for a in result.quality.assessments:
            with st.expander(f'{a.requirement_id} | {a.quality_score}/100 | {a.status}'):
                for f in a.findings:
                    st.write(f.severity, f.criterion, f.explanation)
                    st.code(f.evidence,language=None)
                    st.write(f.recommendation)
    with consistency:
        for f in result.consistency.findings:
            with st.expander(f'{f.id} | {f.severity} | {f.title}'):
                st.write(f.explanation)
                for e in f.evidence:
                    st.caption(f'{e.requirement_id} | {e.source}')
                    st.code(e.quote,language=None)
                st.write(f.recommendation)
    with register:
        st.dataframe([r.model_dump() for r in result.extraction.requirements],hide_index=True)
    with graph_tab:
        st.caption('Предварительные связи — не проверены отдельным аудитором.')
        cats=st.multiselect('Типы связей',list(RELATIONS),default=list(RELATIONS),format_func=lambda x:RELATIONS[x][0],key='quick_rel')
        levels=['critical','high','medium','low']
        sev=st.multiselect('Серьёзность',levels,default=levels,key='quick_sev')
        connected=st.checkbox('Только связанные требования',value=True,key='quick_connected')
        graph=preliminary_graph(result,cats,sev,connected)
        if graph.number_of_nodes():
            components.html(render_graph_html(graph),height=650,scrolling=True)
            st.download_button('Скачать предварительный граф',graph_json(graph),'quick_graph.json','application/json')
        else:
            st.info('Нет связей по выбранным фильтрам.')
