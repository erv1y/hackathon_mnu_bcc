import hashlib
import json
import time
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from core.i18n import tr, localize_graph
import streamlit as st
import streamlit.components.v1 as components
from core.knowledge_graph import build_knowledge_graph, graph_json, render_graph_html, RELATIONS
from orchestrator import run_validation, CACHE_DIR, DeepRunResult, QuickRunResult
from core.advanced_report import generate_pdf, generate_quick_pdf
from core.localized_quick_view import show_quick_result
from core.agent_animation import Scene, render_scene

st.set_page_config(page_title='RequirGents', page_icon='✓', layout='wide', initial_sidebar_state='expanded')
for key in ('result', 'pdf_bytes', 'file_identity', 'run_events', 'report_name'):
    if key not in st.session_state:
        st.session_state[key] = None
if "lang" not in st.session_state:
    st.session_state.lang = "kk"

st.markdown("""
<style>
    .block-container {
        padding-top: 4.5rem !important;
    }
</style>
""", unsafe_allow_html=True)

header_left, header_right = st.columns([5, 1])

with header_right:
    lang = st.selectbox(
        "Language",
        options=["kk", "ru", "en"],
        format_func=lambda x: {
            "kk": "Қазақша",
            "ru": "Русский",
            "en": "English"
        }[x],
        key="lang",
        label_visibility="collapsed"
    )
def t(key):
    return tr(key, lang)

SEVERITY = {'critical': t('Критический'), 'high': t('Высокий'), 'medium': t('Средний'), 'low': t('Низкий')}
READINESS = {'ready': t('Готово к разработке'), 'conditionally_ready': t('Условно готово'), 'not_ready': t('Не готово к разработке')}
DECISIONS = {'confirmed': t('Подтверждено'), 'revised': t('Скорректировано'), 'rejected': t('Отклонено'), 'merged': t('Объединено')}
TYPES = {'functional': t('Функциональное'), 'non_functional': t('Нефункциональное'), 'business_rule': t('Бизнес-правило'), 'acceptance_criterion': t('Критерий приёмки'), 'other': t('Прочее')}


# BCC Business-inspired workspace. No official logo or external assets.
# HTML is static; document and model content is rendered by native widgets.
st.markdown("""
<style>
:root {--bank-green:#008F50; --bank-ink:#17232E; --bank-muted:#68747D; --bank-line:#E3E6E9;}
.block-container {max-width:1500px; padding:2.2rem 2.5rem 2rem;}
[data-testid="stAppViewContainer"] {background:#fff;}
[data-testid="stHeader"] {background:#fff;}
[data-testid="stSidebar"] {background:#071C2B; border-right:none;}
[data-testid="stSidebar"] [data-testid="stSidebarContent"] {padding-top:1.2rem;}
[data-testid="stSidebar"] p,[data-testid="stSidebar"] label,[data-testid="stSidebar"] h3 {color:#EDF2F4;}
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {color:#B2C2CC;}
[data-testid="stSidebar"] [role="radiogroup"] {gap:5px;}
[data-testid="stSidebar"] [role="radiogroup"] label {padding:9px 10px; border-radius:4px; width:100%;}
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {background:#18384A; border-left:3px solid #00B66A;}
[data-testid="stSidebar"] hr {border-color:#29404F;}
[data-testid="stSidebar"] button,[data-testid="stSidebar"] summary {color:#EDF2F4;}
[data-testid="stSidebar"] details {background:transparent;border-color:#29404F;}
h1,h2,h3 {letter-spacing:-.025em; font-weight:620; color:#17232E;}
h1 {font-size:2rem!important; padding:0!important; margin:0!important;}
h2 {font-size:1.55rem!important; padding:.1rem 0!important;}
h3 {font-size:1.12rem!important;}
[data-testid="stMetric"] {padding:4px 10px 4px 0; background:transparent; border:0; border-radius:0; min-height:0;}
[data-testid="stMetricValue"] {font-size:1.9rem; font-weight:600; color:#17232E; letter-spacing:-.035em;}
[data-testid="stMetricLabel"] p {font-size:.85rem; color:#596773;}
[data-testid="stFileUploaderDropzone"] {background:#F5F6F7; border:1px dashed #BCC6CA; border-radius:5px; padding:1rem;}
[data-testid="stExpander"] {border:1px solid #E3E6E9; border-radius:5px; box-shadow:none;}
[data-testid="stDataFrame"] {border:1px solid #E3E6E9; border-radius:4px;}
[data-baseweb="tab-list"] {gap:20px; flex-wrap:wrap; border-bottom:1px solid #E3E6E9;}
[data-baseweb="tab"] {font-weight:500; padding:10px 2px;}
[data-testid="stButton"] button,[data-testid="stDownloadButton"] button {border-radius:5px; min-height:40px; font-weight:550; box-shadow:none;}
[data-testid="stButton"] button[kind="primary"],[data-testid="stDownloadButton"] button[kind="primary"] {background:#008F50; color:white; border-color:#008F50;}
[data-testid="stButton"] button:disabled {opacity:.55;}
[data-testid="stCode"] {font-size:.86rem;}
.workspace-brand {display:flex;align-items:center;gap:10px;margin:3px 0 24px;}
.workspace-mark {width:5px;height:32px;background:#D5AC61;border-radius:1px;}
.workspace-name {font-size:21px;line-height:1.1;font-weight:650;color:#fff;letter-spacing:-.03em;}
.workspace-sub {font-size:11px;letter-spacing:.01em;color:#B2C2CC;margin-top:5px;}
.workspace-kicker {font-size:12px;color:#68747D;margin-bottom:7px;}
.risk-table {width:100%;border-collapse:collapse;font-size:14px;}
.risk-table td {padding:11px 0;border-bottom:1px solid #E3E6E9;}
.risk-dot {display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:10px;}
@media(max-width:800px) {.block-container {padding:2rem 1rem;}}
</style>
""", unsafe_allow_html=True)
with st.sidebar:
    st.markdown('<div class="workspace-brand"><span class="workspace-mark"></span><div><div class="workspace-name">RequirGents</div><div class="workspace-sub">' + t('Рабочее место аналитика') + '</div></div></div>', unsafe_allow_html=True)
    workspace = st.radio('Workspace', ['new', 'saved'], format_func=lambda x: t('Новый анализ') if x == 'new' else t('Открыть отчёт'), key='workspace_mode', label_visibility='collapsed')
    st.divider()
    st.caption(t('О ПРОЕКТЕ'))
    with st.expander(t('Как проводится проверка')):
        st.markdown(t('1. Извлечение требований\n2. Оценка качества\n3. Поиск противоречий\n4. Аудит и программная перепроверка'))
    st.caption('BCC-HUB × MNU FinTech Hackathon')
    st.caption(t('Прототип · v0.8.3\nНе официальный сервис банка.').replace('v0.8.3', 'v0.8.6'))

st.markdown('<div class="workspace-kicker">' + t('Рабочее место / Проверка требований') + '</div>', unsafe_allow_html=True)

# Existing reports are a read-only presentation mode, not a fresh validation.
view_saved = workspace == 'saved'
imported_result = None
imported_pdf = None
if view_saved:
    st.title('RequirGents')
    st.caption(t('Просмотр сохранённого результата · без обращения к Gemini'))
    with st.expander(t('Файлы отчёта'), expanded=not bool(st.session_state.get('saved_json'))):
        saved_col, pdf_col = st.columns(2)
        saved_col.markdown(t('Отчёт JSON'))
        saved = saved_col.file_uploader('JSON report', type=['json'], key='saved_json', label_visibility='collapsed')
        pdf_col.markdown(t('PDF отчёта (необязательно)'))
        saved_pdf = pdf_col.file_uploader('PDF report', type=['pdf'], key='saved_pdf', label_visibility='collapsed')
        st.caption(t('Открывайте только доверенные отчёты. Исходные документы повторно не проверяются.'))
    if saved is not None:
        try:
            data = json.loads(saved.getvalue())
            if not isinstance(data, dict):
                raise ValueError(t('Ожидается объект JSON.'))
            imported_result = (DeepRunResult if 'audit' in data else QuickRunResult).model_validate(data)
            if 'audit' in data and any(d.final_severity not in SEVERITY for d in imported_result.audit.decisions if d.decision in ('confirmed', 'revised')):
                raise ValueError(t('У сохранённого замечания отсутствует серьёзность.'))
        except Exception as error:
            st.error((t('Не удалось открыть отчёт: ') + f'{type(error).__name__}' + t('. Выберите экспорт JSON из приложения.')))
            imported_result = None
        if imported_result is not None and saved_pdf is not None:
            if saved_pdf.getvalue().startswith(b'%PDF-'):
                imported_pdf = saved_pdf.getvalue()
                st.caption(t('PDF прикреплён вручную; соответствие JSON не проверяется.'))
            else:
                st.warning(t('Файл не распознан как PDF.'))
    if imported_result is None:
        st.stop()
else:
    st.title('RequirGents')
    st.caption(t('Загрузите до пяти документов: PDF, DOCX, Markdown или TXT.'))
    files_col, settings_col = st.columns([3, 2], gap='large')
    with files_col:
        st.markdown(t('Документы'))
        uploaded = st.file_uploader('Documents', type=['pdf', 'docx', 'md', 'txt'], accept_multiple_files=True, key='source_documents', label_visibility='collapsed')
        valid_files = 0 < len(uploaded) <= 5 and len({Path(f.name).name.casefold() for f in uploaded}) == len(uploaded)
        if uploaded:
            st.caption((t('Выбрано документов: ') + f'{len(uploaded)}'))
            for f in uploaded:
                st.text(f'{f.name} · {f.size/1024:.1f} KB')
        if uploaded and not valid_files:
            st.error(t('Выберите не более 5 файлов с уникальными именами.'))
    with settings_col:
        st.markdown(t('Режим проверки'))
        mode = st.radio('Validation mode', ['deep', 'quick'], key='validation_mode', label_visibility='collapsed', format_func=lambda m: t('Deep — полный анализ') if m == 'deep' else t('Quick — предварительный анализ'))
        st.caption(t('Полная проверка с аудитом') if mode == 'deep' else t('Быстрая предварительная оценка без аудита'))
        cache = st.checkbox(t('Локальный кэш и восстановление (SQLite)'), value=False, key='use_local_cache')
        st.caption(t('Кэш не зашифрован и сохраняется после закрытия приложения.'))
        force = st.checkbox(t('Начать заново, не использовать кэш'), disabled=not cache, key='force_reanalysis')
    consent = st.checkbox(t('Разрешаю передачу текста в Gemini API. Документы разрешено передавать внешнему сервису.'), key='external_consent')
    st.caption(t('Не загружайте конфиденциальные документы без разрешения.'))
    identity = (mode, tuple(sorted((f.name, hashlib.sha256(f.getvalue()).hexdigest()) for f in uploaded)))
    start = st.button(t('Проверить документы'), type='primary', disabled=not valid_files or not consent, use_container_width=True)

    if start:
        for key in ('result', 'pdf_bytes', 'file_identity', 'run_events', 'report_name'):
            st.session_state[key] = None
        started = time.perf_counter()
        progress = st.progress(0, text=t('Подготовка...'))
        stage = st.empty()
        timer = st.empty()
        animation = st.empty()
        scene = Scene(mode=mode)
        events = []

        def draw_scene():
            # Streamlit replaces this frame on actual stage transitions only.
            with animation.container():
                components.html(render_scene(scene, lang), height=620, scrolling=False)

        draw_scene()

        def update(event):
            events.append(event.stage)
            progress.progress(event.percent, text=t(event.message))
            stage.info(t(event.stage))
            timer.caption((t('Текущий запуск: ') + f'{time.perf_counter() - started:.1f}' + t(' сек. (при обновлении этапа)')))
            if scene.update(event):
                draw_scene()

        try:
            with TemporaryDirectory() as tmp:
                root = Path(tmp)
                input_paths = []
                for f in uploaded:
                    path = root / Path(f.name).name
                    path.write_bytes(f.getvalue())
                    input_paths.append(path)
                output = root / 'results'
                result = run_validation(input_paths, progress_callback=update, reports_dir=output,
                                        use_cache=cache, force_reanalysis=cache and force, mode=mode)
                progress.progress(95, text=t('Формирование PDF...'))
                if scene.phase not in ('report', 'cache'):
                    scene.finish('report')
                    draw_scene()
                # Keep valid JSON available even if PDF generation fails.
                st.session_state.result = result
                st.session_state.file_identity = identity
                st.session_state.run_events = events
                st.session_state.report_name = f'{input_paths[0].stem}_report.pdf' if len(input_paths) == 1 else 'combined_report.pdf'
                if mode == 'quick':
                    st.session_state.report_name = 'quick_preliminary_report.pdf'
                    pdf = generate_quick_pdf(result, output / 'quick_report.pdf')
                else:
                    pdf = generate_pdf(result, output / 'validation_report.pdf')
                st.session_state.pdf_bytes = pdf.read_bytes()
            scene.finish('done')
            draw_scene()
            progress.progress(100, text=t('Анализ завершён!'))
            stage.success(t('Готово'))
            timer.caption((t('Текущий запуск с PDF: ') + f'{time.perf_counter() - started:.1f}' + t(' сек.')))
        except Exception as error:
            scene.finish('export_error' if st.session_state.result is not None else 'error')
            draw_scene()
            stage.error(t('PDF недоступен; JSON сохранён.') if st.session_state.result is not None else t('Выполнение остановлено.'))
            st.error(f'{type(error).__name__}: {error}')
            if cache:
                st.info(t('Для восстановления загрузите тот же набор файлов с теми же именами, оставьте локальное сохранение включённым, снимите «Начать заново» и повторите запуск.'))
            else:
                st.info(t('Постоянное сохранение выключено; следующий запуск начнётся заново.'))

if view_saved:
    result = imported_result
    mode = 'deep' if isinstance(result, DeepRunResult) else 'quick'
    pdf_bytes = imported_pdf
    report_name = saved_pdf.name if imported_pdf is not None else 'saved_report.pdf'
    events = []
else:
    result = st.session_state.result
    pdf_bytes = st.session_state.pdf_bytes
    report_name = st.session_state.report_name
    events = st.session_state.run_events or []
    if result is not None and identity != st.session_state.file_identity:
        result = None
        st.info(t('Выберите ранее проверенный документ или запустите новый анализ.'))

if result is not None:
    st.caption(t('Тексты отчёта и исходные цитаты сохранены на языке анализа.'))

if result is not None and mode == 'quick':
    show_quick_result(result, pdf_bytes, events, lang=lang)
    st.stop()

if result is not None:
    retained = [d for d in result.audit.decisions if d.decision in ('confirmed', 'revised')]
    counts = Counter(d.final_severity for d in retained)
    decisions = Counter(d.decision for d in result.audit.decisions)
    scores = [a.quality_score for a in result.quality.assessments]
    requirements = {r.id: r for r in result.extraction.requirements}
    st.divider()
    heading, exports = st.columns([3, 2], gap='medium')
    with heading:
        st.header(t('Результаты проверки'))
        st.text(', '.join(result.source_files))
        if view_saved:
            st.caption(t('Сохранённый результат · исходный анализ не повторялся'))
        elif 'Cache Hit' in events:
            st.caption(t('Результат восстановлен из кэша, без новых запросов'))
        elif 'Resume' in events:
            st.caption(t('Проверка продолжена из сохранённого состояния'))
    with exports:
        pdf_col, json_col = st.columns(2)
        if pdf_bytes:
            pdf_col.download_button(t('Скачать PDF'), pdf_bytes, report_name, 'application/pdf', type='primary', use_container_width=True)
        else:
            pdf_col.caption(t('PDF недоступен'))
        json_col.download_button(t('Скачать JSON'), result.model_dump_json(indent=2).encode('utf-8'), 'validation_results.json', 'application/json', use_container_width=True)
    cross = [d for d in retained if len({requirements[rid].source for rid in d.requirement_ids if rid in requirements}) > 1]
    review_count = sum(f.finding_id.startswith('REVIEW-') for f in retained)
    readiness = result.audit.readiness
    a,b,c,d = st.columns(4)
    a.metric(t('Требования'), len(requirements))
    b.metric(t('Замечания и кандидаты'), len(retained))
    c.metric(t('Оценка до аудита'), f'{sum(scores)/len(scores):.1f}/100' if scores else '—')
    d.metric(t('Время анализа'), (f'{result.execution_time_seconds:.1f}' + t(' с')))
    st.caption(t('Оценка качества требований до аудита не является точностью валидатора.'))
    {'ready': st.success, 'conditionally_ready': st.warning, 'not_ready': st.error}[readiness](READINESS[readiness])
    st.caption((t('На ручное подтверждение: ') + f'{review_count}' + t(' · Междокументные: ') + f'{len(cross)}' + t(' · Объединено: ') + f"{decisions['merged']}" + t(' · Отклонено: ') + f"{decisions['rejected']}"))
    if len(result.source_files) > 1:
        st.caption(t('Префикс D01:: обозначает первый документ по сортировке имён.'))
    summary, findings, register, graph_tab, audit, methodology = st.tabs([t('Обзор'), t('Замечания'), t('Требования'), t('Граф связей'), t('Журнал аудита'), t('Методология')])
    with summary:
        st.subheader(t('Приоритетные замечания'))
        priority = sorted(retained, key=lambda f: list(SEVERITY).index(f.final_severity))[:4]
        if priority:
            st.dataframe([{'ID':f.finding_id, t('Замечание'):f.title, t('Серьёзность'):SEVERITY[f.final_severity], t('Статус'):t('Ручное подтверждение') if f.finding_id.startswith('REVIEW-') else DECISIONS[f.decision]} for f in priority], hide_index=True, use_container_width=True)
        else:
            st.caption(t('Нет сохранённых замечаний.'))
        actions, risks = st.columns([3,2], gap='large')
        with actions:
            st.subheader(t('Что необходимо исправить'))
            for n, action in enumerate(result.audit.priority_actions, 1):
                st.write(f'{n}. {action}')
            if not result.audit.priority_actions:
                st.caption(t('Приоритетные действия не указаны.'))
        with risks:
            st.subheader(t('Серьёзность замечаний'))
            risk_colors = {'critical':'#9E2530','high':'#CF4141','medium':'#BC8224','low':'#788590'}
            risk_rows = ''.join(f'<tr><td><span class="risk-dot" style="background:{risk_colors[k]}"></span>{SEVERITY[k]}</td><td style="text-align:right;font-weight:600">{counts[k]}</td></tr>' for k in SEVERITY)
            st.markdown(f'<table class="risk-table">{risk_rows}</table>', unsafe_allow_html=True)
            st.caption(t('С учётом программных кандидатов.'))
        with st.expander(t('Итоговый вывод и ограничения')):
            st.write(result.audit.executive_summary)
            for limitation in result.audit.limitations:
                st.write(limitation)
            st.caption(t('Результаты требуют проверки специалистом.'))
    with findings:
        st.subheader(t('Реестр замечаний'))
        filter_col, search_col = st.columns([2,3])
        levels = filter_col.multiselect(t('Серьёзность'), list(SEVERITY), default=list(SEVERITY), format_func=SEVERITY.get, key='finding_levels')
        query = search_col.text_input(t('Поиск по ID, названию или требованию'), key='finding_search').strip().casefold()
        shown = [f for f in sorted(retained, key=lambda x: list(SEVERITY).index(x.final_severity)) if f.final_severity in levels and query in ' '.join([f.finding_id, f.title, f.explanation, *f.requirement_ids]).casefold()]
        st.caption((t('Показано ') + f'{len(shown)}' + t(' из ') + f'{len(retained)}'))
        if shown:
            st.dataframe([{'ID':f.finding_id, t('Серьёзность'):SEVERITY[f.final_severity], t('Замечание'):f.title, t('Статус'):t('Ручное подтверждение') if f.finding_id.startswith('REVIEW-') else DECISIONS[f.decision]} for f in shown], hide_index=True, use_container_width=True)
            by_id = {f.finding_id:f for f in shown}
            selected = st.selectbox(t('Открыть замечание'), list(by_id), format_func=lambda k: f'{k} — {by_id[k].title}', key='selected_finding')
            f = by_id[selected]
            is_review = f.finding_id.startswith('REVIEW-')
            st.divider()
            st.subheader(f.title)
            st.caption(f'{f.finding_id} · {SEVERITY[f.final_severity]} · ' + (t('Требуется ручное подтверждение') if is_review else DECISIONS[f.decision]))
            st.markdown(t('**Проблема**'))
            st.write(f.explanation)
            st.markdown(t('**Рекомендация**'))
            st.write(f.recommendation)
            st.markdown(t('**Доказательства и источники**'))
            if f.final_evidence:
                for evidence in f.final_evidence:
                    st.text((f'{evidence.requirement_id}' + ' · ' + f'{evidence.source}' + t(' · стр. ') + f"{evidence.page or '—'}" + t(' · абз. ') + f"{evidence.paragraph or '—'}"))
                    st.code(evidence.quote, language=None)
            else:
                st.caption(t('Итоговые цитаты отсутствуют; ниже текст связанных требований.'))
                for rid in f.requirement_ids:
                    r = requirements.get(rid)
                    if r:
                        st.text((f'{r.id}' + ' · ' + f'{r.source}' + t(' · стр. ') + f"{r.page or '—'}" + t(' · абз. ') + f"{r.paragraph or '—'}"))
                        st.code(r.text, language=None)
            with st.expander(t('Обоснование решения')):
                st.write(f.audit_reason)
                if not is_review:
                    st.caption((t('Уверенность модели: ') + f'{f.confidence:.0%}' + t(' (экспертная оценка)')))
        else:
            st.info(t('Нет замечаний по выбранным фильтрам.'))
    with register:
        st.subheader(t('Реестр требований'))
        rq = st.text_input(t('Поиск по реестру'), key='requirements_search').strip().casefold()
        assessments = {a.requirement_id: a for a in result.quality.assessments}
        st.dataframe([{'ID': r.id, t('Тип'): TYPES[r.type], t('Текст'): r.text, t('Источник'): r.source,
                       t('Страница'): r.page, t('Абзац'): r.paragraph,
                       t('Quality Score'): assessments[r.id].quality_score if r.id in assessments else None,
                       t('Статус до аудита'): t(assessments[r.id].status) if r.id in assessments else None}
                      for r in requirements.values() if rq in ' '.join([r.id,r.text,r.source]).casefold()], hide_index=True, use_container_width=True)
    with graph_tab:
        st.subheader(t('Граф связей требований'))
        st.caption(t('Связи из сохранённых замечаний согласованности, включая программные кандидаты. Отклонённые и объединённые замечания не отображаются отдельно.'))
        categories = st.multiselect(t('Типы связей'), list(RELATIONS), default=list(RELATIONS), format_func=lambda x: t(RELATIONS[x][0]), key='graph_categories')
        severity_filter = st.multiselect(t('Серьёзность связей'), list(SEVERITY), default=list(SEVERITY), format_func=SEVERITY.get, key='graph_severity')
        connected = st.checkbox(t('Только связанные требования'), value=True, key='graph_connected')
        graph = build_knowledge_graph(result, categories, severity_filter, connected)
        req_count = sum(a['kind'] == 'requirement' for _, a in graph.nodes(data=True))
        issue_ids = {a['finding_id'] for _, _, a in graph.edges(data=True)}
        for col, label, value in zip(st.columns(3), [t('Требования'), t('Замечания'), t('Рёбра')], [req_count, len(issue_ids), graph.number_of_edges()]):
            col.metric(label, value)
        if graph.number_of_nodes():
            try:
                components.html(render_graph_html(localize_graph(graph, lang)), height=650, scrolling=True)
            except Exception as error:
                st.warning((t('Не удалось отобразить граф: ') + f'{error}'))
            st.download_button(t('Скачать граф JSON'), graph_json(graph).encode('utf-8'), 'knowledge_graph.json', 'application/json')
        else:
            st.info(t('Нет связей, соответствующих выбранным фильтрам.'))
        st.caption(t('Круг — требование; ромб — замечание о группе требований или возможном пропуске. Красный — противоречие, синий — дублирование. Цвет вершины соответствует типу требования.'))
        relation_rows = {}
        for u, v, data in graph.edges(data=True):
            row = relation_rows.setdefault(data['finding_id'], {'ID': data['finding_id'], t('Связь'): t(data['label']), t('Серьёзность'): SEVERITY.get(data['severity'], '—'), t('Требования'): set(), t('Описание'): data['explanation']})
            row[t('Требования')].update(n[4:] for n in (u,v) if n.startswith('req:'))
        if relation_rows:
            st.dataframe([{**row, t('Требования'): ', '.join(sorted(row[t('Требования')]))} for row in relation_rows.values()], hide_index=True, use_container_width=True)
        st.info(t('Граф отражает обнаруженные AI связи, а не полный перечень зависимостей. Отсутствие ребра не доказывает отсутствие связи.'))
    with audit:
        for col, decision in zip(st.columns(4), DECISIONS):
            col.metric(t('Скорректировано / кандидаты') if decision == 'revised' else DECISIONS[decision], decisions[decision])
        st.dataframe([{'ID': d.finding_id, t('Решение'): t('Ручное подтверждение') if d.finding_id.startswith('REVIEW-') else DECISIONS[d.decision], t('Название'): d.title,
                       t('Серьёзность'): SEVERITY.get(d.final_severity, '—'), t('Обоснование'): d.audit_reason,
                       t('Объединено с'): d.merged_into} for d in result.audit.decisions], hide_index=True, use_container_width=True)
        st.write(t(result.audit.extraction_audit.status), result.audit.extraction_audit.comments)
        for item in result.audit.extraction_audit.missing_requirements:
            st.warning((t('Возможный пропуск: ') + f'{item}'))
        for item in result.audit.extraction_audit.unsupported_requirements:
            st.warning((t('Неподтверждённое требование: ') + f'{item}'))
    with methodology:
        st.markdown(t('**LangGraph:** Extractor → параллельные Quality и Consistency → Auditor.\n\n**Надёжность:** Structured Output, Pydantic, проверка цитат, повторные попытки и резервные модели.\n\n**Кэш:** SHA-256 документа, имени и конфигурации. При изменении кода или промптов создаётся новый ключ. При сбое повторяются незавершённые узлы.\n\nВремя на панели и в PDF относится к текущему запуску (включая восстановление), а не сумме всех предыдущих попыток. Индикатор отражает этапы, а не оставшееся время.'))
        st.info(t('Серьёзность и уверенность AI не являются сертифицированными оценками. Программные правила имеют ограниченный охват.'))
        with st.expander(t('Паспорт проверки')):
            st.json(result.passport)
st.divider()
st.caption(t('RequirGents · v0.8.3 · Хакатон BCC-HUB × MNU').replace('v0.8.3', 'v0.8.6'))
