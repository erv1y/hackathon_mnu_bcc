"""Append run passport and source traceability to the existing PDF reports."""
from pathlib import Path
from tempfile import TemporaryDirectory
from html import escape
import os
from core.pdf_labels import pdf_label


def append_sections(result, output_path, base_generator):
    import pymupdf as fitz
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, LongTable, TableStyle
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib import colors
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    font = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'arial.ttf'
    if not font.exists():
        font = Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    pdfmetrics.registerFont(TTFont('PassportUnicode', str(font)))
    body = ParagraphStyle('PassportBody', fontName='PassportUnicode', fontSize=9, leading=13, spaceAfter=5, wordWrap='CJK')
    heading = ParagraphStyle('PassportHeading', parent=body, fontSize=16, leading=21, textColor=colors.HexColor('#16836c'), spaceBefore=12)
    story = []
    def para(value):
        return Paragraph(escape(str(value)).replace('\n','<br/>'), body)
    def add(value):
        story.append(para(value))
    def title(value):
        story.append(Paragraph(escape(pdf_label(value, language)), heading))
    def table(rows, widths):
        t = LongTable([[para(x) for x in row] for row in rows], colWidths=widths, repeatRows=1, splitInRow=1)
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#dceee9')), ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f3f6f5')]), ('VALIGN',(0,0),(-1,-1),'TOP'), ('LINEBELOW',(0,0),(-1,0),0.6,colors.HexColor('#16836c'))]))
        story.append(t)
    p = getattr(result,'passport',{})
    language = p.get('language', 'ru')
    title('Паспорт прогона')
    add('Документы: '+', '.join(result.source_files))
    add('Режим: '+str(p.get('mode','не записан'))+'; тип запуска: '+str(p.get('run_kind','не записан')))
    add('Начало исходного анализа (UTC): '+str(p.get('analysis_started_utc') or 'не записано'))
    add('Дата текущего прогона (UTC): '+str(p.get('report_run_utc') or 'не записана'))
    add('Время текущего запуска: '+str(result.execution_time_seconds)+' сек. (без PDF)')
    for label, key in [('Исходная версия','analysis_revision'),('Текущая версия','current_revision')]:
        rev=p.get(key) or {}
        add(label+': '+str(rev.get('commit') or 'коммит не определён')+'; незакоммиченные изменения: '+str(rev.get('dirty')))
    add('Настроенная модель: '+str(p.get('configured_model') or 'не записана'))
    rows=[['Этап','Модель успешного запроса','Версия по ответу API']]
    for stage, calls in p.get('successful_responses',{}).items():
        if not calls: rows.append([stage,'не записана','—'])
        for c in calls: rows.append([stage+(' / '+c['source'] if c.get('source') else ''),c['requested_model'],c.get('reported_version') or 'API не сообщил'])
    table(rows,[135,185,175])
    add('Список отражает успешные ответы завершённых узлов, включая fallback, а не все попытки и ошибки API.')
    def location(r):
        value = r['source']
        if r.get('page') is not None: value += f" / стр. {r['page']}"
        if r.get('line_start') is not None:
            value += f" / строки {r['line_start']}–{r['line_end']}"
        elif r.get('paragraph') is not None:
            value += f" / блок {r['paragraph']}"
        if r.get('section'): value += ' / ' + r['section']
        return value
    mapping = {r['report_id']: r for r in p.get('id_mapping', [])}
    title('Соответствие идентификаторов')
    rows=[['ID отчёта','ID документа','Источник','Цитата']]
    for r in p.get('id_mapping',[]):
        rows.append([r['report_id'],r['original_id'] or 'не установлен',location(r),r['quote']])
    table(rows,[82,78,120,215])
    add('Если исходный идентификатор нельзя подтвердить по тексту, он не выдумывается. Системный ID и цитата сохраняются.')
    if hasattr(result,'audit'):
        title('Итоговые доказательства после аудита')
        add('Этот раздел содержит актуальные цитаты решений. Дополнительные REVIEW-замечания требуют ручного подтверждения. Цитаты объединённых участников сохранены для проверки, но их релевантность не гарантирована.')
        rows=[['Замечание','Источник','Цитата']]
        for d in result.audit.decisions:
            if d.decision not in ('confirmed','revised'): continue
            for e in d.final_evidence:
                rows.append([d.finding_id+' / '+e.requirement_id,location(mapping.get(e.requirement_id,e.model_dump())),e.quote])
        if len(rows)>1: table(rows,[95,150,250])
        else: add('Итоговые цитаты отсутствуют.')
    title('Матрица трассируемости')
    reqs={r.id:r for r in result.extraction.requirements}
    issues={rid:[] for rid in reqs}
    if hasattr(result,'audit'):
        for d in result.audit.decisions:
            if d.decision in ('confirmed','revised'):
                for rid in d.requirement_ids:
                    if rid in issues: issues[rid].append(f'{d.finding_id} | {d.final_severity} | {d.recommendation}')
    else:
        for a in result.quality.assessments:
            for i,f in enumerate(a.findings,1):
                if a.requirement_id in issues: issues[a.requirement_id].append(f'Q:{a.requirement_id}:{i} | {f.severity} | {f.recommendation}')
        for f in result.consistency.findings:
            for rid in f.requirement_ids:
                if rid in issues: issues[rid].append(f'{f.id} | {f.severity} | {f.recommendation}')
    status_names = {'passed': 'Пройдено', 'needs_revision': 'Доработать', 'needs_clarification': 'Уточнить'}
    assessments={a.requirement_id:a for a in result.quality.assessments}
    rows=[['Требование','Источник','Оценка до аудита','Замечания и рекомендации']]
    for rid,r in reqs.items():
        a=assessments.get(rid)
        rows.append([rid,location(mapping.get(rid, r.model_dump())),f'{status_names.get(a.status, a.status)} / {a.quality_score}' if a else 'нет оценки','\n'.join(issues[rid]) or 'Нет сохранённых замечаний'])
    table(rows,[82,115,80,218])
    if not hasattr(result,'audit'): add('Предварительные замечания: независимый аудит не завершён; категории могут пересекаться.')
    from core.report_generator import draw_report_frame
    with TemporaryDirectory() as tmp:
        base=Path(tmp)/'base.pdf'; appendix=Path(tmp)/'appendix.pdf'
        base_generator(result,base)
        with fitz.open(str(base)) as doc:
            preceding = len(doc)

            def frame(canvas, document):
                draw_report_frame(canvas, preceding + document.page, language, font="PassportUnicode",
                                  footer=pdf_label("Паспорт прогона", language))

            SimpleDocTemplate(str(appendix), pagesize=A4, leftMargin=50, rightMargin=50,
                              topMargin=48, bottomMargin=46).build(story, onFirstPage=frame, onLaterPages=frame)
            with fitz.open(str(appendix)) as extra:
                doc.insert_pdf(extra)
                doc.save(str(target))
    return target


def generate_pdf(result, output_path):
    from core.report_generator import generate_pdf as base
    return append_sections(result,output_path,base)


def generate_quick_pdf(result, output_path):
    from core.quick_view import generate_quick_pdf as base
    return append_sections(result,output_path,base)
