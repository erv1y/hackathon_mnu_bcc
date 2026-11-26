import hashlib
import time
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

import streamlit as st
import streamlit.components.v1 as components
from core.knowledge_graph import build_knowledge_graph, graph_json, render_graph_html, RELATIONS
from orchestrator import run_validation, CACHE_DIR
from core.advanced_report import generate_pdf, generate_quick_pdf
from core.quick_view import show_quick_result

st.set_page_config(page_title='AI Requirements Validator', page_icon='🛡️', layout='wide')
for key in ('result', 'pdf_bytes', 'file_identity', 'run_events', 'report_name'):
    if key not in st.session_state:
        st.session_state[key] = None

SEVERITY = {'critical': 'Критический', 'high': 'Высокий', 'medium': 'Средний', 'low': 'Низкий'}
READINESS = {'ready': 'Готово к разработке', 'conditionally_ready': 'Условно готово', 'not_ready': 'Не готово к разработке'}
DECISIONS = {'confirmed': 'Подтверждено', 'revised': 'Скорректировано', 'rejected': 'Отклонено', 'merged': 'Объединено'}
TYPES = {'functional': 'Функциональное', 'non_functional': 'Нефункциональное', 'business_rule': 'Бизнес-правило', 'acceptance_criterion': 'Критерий приёмки', 'other': 'Прочее'}

with st.sidebar:
    st.title('🛡️ AI Validator')
    st.caption('BCC-HUB × MNU FinTech Hackathon')
    st.info('Quick / Deep Validation')
    st.markdown('**LangGraph StateGraph**\n\n1. Extractor\n2. Quality Analyst\n3. Consistency Analyst\n4. Auditor')
    st.divider()
    st.caption('SQLite: локальное восстановление и кэш по желанию пользователя.')
    st.caption(f'Папка кэша: {CACHE_DIR.name}')
    st.caption('Prototype v0.7.0')

st.title('AI Requirements Validator')
st.write('Графовая мультиагентная система проверки технических заданий для FinTech.')
st.header('1. Загрузка документов')
uploaded = st.file_uploader('Загрузите от 1 до 5 документов', type=['pdf', 'docx', 'md', 'txt'], accept_multiple_files=True)
mode = st.radio('Режим проверки', ['deep', 'quick'], format_func=lambda m: 'Deep — 4 агента' if m == 'deep' else 'Quick — предварительно, без аудита', horizontal=True)
identity = (mode, tuple(sorted((f.name, hashlib.sha256(f.getvalue()).hexdigest()) for f in uploaded)))
valid_files = 0 < len(uploaded) <= 5 and len({Path(f.name).name.casefold() for f in uploaded}) == len(uploaded)
if uploaded:
    st.caption(f'Документов: {len(uploaded)}')
    for f in uploaded:
        st.caption(f'{f.name} · {f.size/1024:.1f} KB')
    if not valid_files:
        st.error('Выберите не более 5 файлов с уникальными именами.')
consent = st.checkbox('Разрешаю передачу текста документа в Gemini API. Документ разрешено передавать внешнему сервису.')
cache = st.checkbox('Разрешаю локальное сохранение текста и результатов в SQLite для восстановления и повторного использования.', value=False)
st.caption('При включённом кэше данные остаются на компьютере после закрытия сайта. База не зашифрована. Используйте синтетические документы.')
force = st.checkbox('Начать заново (не использовать сохранённый анализ)', disabled=not cache)
start = st.button('🚀 Запустить / продолжить анализ', type='primary', disabled=not valid_files or not consent)

if start:
    for key in ('result', 'pdf_bytes', 'file_identity', 'run_events', 'report_name'):
        st.session_state[key] = None
    started = time.perf_counter()
    progress = st.progress(0, text='Подготовка...')
    stage = st.empty()
    timer = st.empty()
    labels = ['Quick Agent'] if mode == 'quick' else ['Extractor', 'Quality Analyst', 'Consistency Analyst', 'Auditor']
    cols = st.columns(len(labels))
    slots = [col.empty() for col in cols]
    for slot, name in zip(slots, labels):
        slot.info(f'⏳ {name}')
    events = []

    def update(event):
        events.append(event.stage)
        progress.progress(event.percent, text=event.message)
        stage.info(event.stage)
        timer.caption(f'Текущий запуск: {time.perf_counter()-started:.1f} сек. (при обновлении этапа)')
        if mode == 'quick':
            if event.percent >= 85:
                slots[0].success('✓ Quick Agent' + (' · кэш' if event.stage == 'Cache Hit' else ''))
            else:
                slots[0].warning('Quick Agent')
            return
        if event.stage == 'Cache Hit':
            for slot, name in zip(slots, labels):
                slot.success(f'✓ {name} · кэш')
        elif event.percent >= 85:
            for slot, name in zip(slots, labels):
                slot.success(f'✓ {name}')
        elif event.percent >= 75:
            for slot, name in zip(slots[:3], labels[:3]):
                slot.success(f'✓ {name}')
            slots[3].warning('⚙️ Auditor')
        elif event.percent >= 35:
            slots[0].success('✓ Extractor')
            slots[1].warning('⚙️ Quality Analyst')
            slots[2].warning('⚙️ Consistency Analyst')
            if event.percent == 55:
                index = 1 if 'Quality Analyst' in event.message else 2
                slots[index].success(f'✓ {labels[index]}')
        elif event.percent == 15:
            slots[0].warning('⚙️ Extractor')

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
            progress.progress(95, text='Формирование PDF...')
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
        progress.progress(100, text='Анализ завершён!')
        stage.success('Готово')
        timer.caption(f'Текущий запуск с PDF: {time.perf_counter()-started:.1f} сек.')
    except Exception as error:
        stage.error('Выполнение остановлено.')
        st.error(f'{type(error).__name__}: {error}')
        if cache:
            st.info('Для восстановления загрузите тот же набор файлов с теми же именами, оставьте локальное сохранение включённым, снимите «Начать заново» и повторите запуск.')
        else:
            st.info('Постоянное сохранение выключено; следующий запуск начнётся заново.')

result = st.session_state.result
if result is not None and identity != st.session_state.file_identity:
    result = None
    st.info('Выберите ранее проверенный документ или запустите новый анализ.')

if result is not None and mode == 'quick':
    show_quick_result(result, st.session_state.pdf_bytes, st.session_state.run_events or [])
    st.stop()

if result is not None:
    retained = [d for d in result.audit.decisions if d.decision in ('confirmed', 'revised')]
    counts = Counter(d.final_severity for d in retained)
    decisions = Counter(d.decision for d in result.audit.decisions)
    scores = [a.quality_score for a in result.quality.assessments]
    requirements = {r.id: r for r in result.extraction.requirements}
    events = st.session_state.run_events or []
    st.header('2. Результаты анализа')
    if 'Cache Hit' in events:
        st.success('Cache Hit — результат восстановлен без новых AI-запросов.')
    elif 'Resume' in events:
        st.success('Resume — анализ продолжен из сохранённого состояния.')
    st.caption('Документы: ' + ', '.join(result.source_files))
    cross = [d for d in retained if len({requirements[rid].source for rid in d.requirement_ids if rid in requirements}) > 1]
    st.metric('Междокументные замечания', len(cross))
    if len(result.source_files) > 1:
        st.caption('ID D01::FR-01 означает исходный ID FR-01 в первом документе. Нумерация определяется сортировкой имён файлов.')
    a, b, c, d = st.columns(4)
    a.metric('Требований', len(requirements))
    b.metric('Замечаний', len(retained))
    c.metric('Quality Score до аудита', f'{sum(scores)/len(scores):.1f}/100' if scores else '—')
    d.metric('Время текущего запуска', f'{result.execution_time_seconds:.1f} сек.')
    readiness = result.audit.readiness
    {'ready': st.success, 'conditionally_ready': st.warning, 'not_ready': st.error}[readiness](READINESS[readiness])
    for col, severity in zip(st.columns(4), SEVERITY):
        col.metric(severity.capitalize(), counts[severity])
    st.bar_chart({'Количество': {s.capitalize(): counts[s] for s in SEVERITY}})
    a, b = st.columns(2)
    if st.session_state.pdf_bytes:
        a.download_button('Скачать PDF', st.session_state.pdf_bytes, st.session_state.report_name, 'application/pdf', type='primary')
    b.download_button('Скачать JSON', result.model_dump_json(indent=2).encode('utf-8'), 'validation_results.json', 'application/json')
    summary, findings, register, graph_tab, audit, methodology = st.tabs(['Summary', 'Findings', 'Requirements', 'Knowledge Graph', 'Audit Trail', 'Methodology'])
    with summary:
        st.subheader('Приоритетные действия')
        for n, action in enumerate(result.audit.priority_actions, 1):
            st.write(f'{n}. {action}')
        st.subheader('Ограничения')
        for limitation in result.audit.limitations:
            st.write(limitation)
        st.warning('Результаты AI требуют проверки специалистами.')
    with findings:
        levels = st.multiselect('Серьёзность', list(SEVERITY), default=list(SEVERITY), format_func=SEVERITY.get)
        query = st.text_input('Поиск замечаний').strip().lower()
        shown = 0
        for f in sorted(retained, key=lambda x: list(SEVERITY).index(x.final_severity)):
            if f.final_severity not in levels or query not in (' '.join([f.finding_id, f.title, f.explanation, *f.requirement_ids])).lower():
                continue
            shown += 1
            with st.expander(f'{f.finding_id} | {SEVERITY[f.final_severity]} | {f.title}'):
                st.write('Проблема:', f.explanation)
                st.write('Рекомендация:', f.recommendation)
                st.write('Решение аудитора:', DECISIONS[f.decision], f.audit_reason)
                for rid in f.requirement_ids:
                    r = requirements.get(rid)
                    if r:
                        st.write(r.id)
                        st.code(r.text, language=None)
                        st.caption(f'{r.source} · страница {r.page or "—"} · абзац {r.paragraph or "—"}')
                st.caption(f'Уверенность AI: {f.confidence:.0%} (экспертная оценка)')
        st.caption(f'Показано {shown} из {len(retained)}')
    with register:
        assessments = {a.requirement_id: a for a in result.quality.assessments}
        st.dataframe([{'ID': r.id, 'Тип': TYPES[r.type], 'Текст': r.text, 'Источник': r.source,
                       'Страница': r.page, 'Абзац': r.paragraph,
                       'Quality Score': assessments[r.id].quality_score if r.id in assessments else None,
                       'Статус до аудита': assessments[r.id].status if r.id in assessments else None}
                      for r in requirements.values()], hide_index=True, use_container_width=True)
    with graph_tab:
        st.subheader('Requirements Knowledge Graph')
        st.caption('Связи из замечаний Consistency Analyst, сохранённых после аудита. Отклонённые и объединённые замечания не отображаются отдельно. Категории взяты из исходного анализа.')
        categories = st.multiselect('Типы связей', list(RELATIONS), default=list(RELATIONS), format_func=lambda x: RELATIONS[x][0], key='graph_categories')
        severity_filter = st.multiselect('Серьёзность связей', list(SEVERITY), default=list(SEVERITY), format_func=SEVERITY.get, key='graph_severity')
        connected = st.checkbox('Только связанные требования', value=True, key='graph_connected')
        graph = build_knowledge_graph(result, categories, severity_filter, connected)
        req_count = sum(a['kind'] == 'requirement' for _, a in graph.nodes(data=True))
        issue_ids = {a['finding_id'] for _, _, a in graph.edges(data=True)}
        for col, label, value in zip(st.columns(3), ['Требования', 'Замечания', 'Рёбра'], [req_count, len(issue_ids), graph.number_of_edges()]):
            col.metric(label, value)
        if graph.number_of_nodes():
            try:
                components.html(render_graph_html(graph), height=650, scrolling=True)
            except Exception as error:
                st.warning(f'Не удалось отобразить граф: {error}')
            st.download_button('Скачать граф JSON', graph_json(graph).encode('utf-8'), 'knowledge_graph.json', 'application/json')
        else:
            st.info('Нет связей, соответствующих выбранным фильтрам.')
        st.caption('Круг — требование; ромб — замечание о группе требований или возможном пропуске. Красный — противоречие, синий — дублирование. Цвет вершины соответствует типу требования.')
        relation_rows = {}
        for u, v, data in graph.edges(data=True):
            row = relation_rows.setdefault(data['finding_id'], {'ID': data['finding_id'], 'Связь': data['label'], 'Серьёзность': SEVERITY.get(data['severity'], '—'), 'Требования': set(), 'Описание': data['explanation']})
            row['Требования'].update(n[4:] for n in (u,v) if n.startswith('req:'))
        if relation_rows:
            st.dataframe([dict(row, Требования=', '.join(sorted(row['Требования']))) for row in relation_rows.values()], hide_index=True, use_container_width=True)
        st.info('Граф отражает обнаруженные AI связи, а не полный перечень зависимостей. Отсутствие ребра не доказывает отсутствие связи.')
    with audit:
        for col, decision in zip(st.columns(4), DECISIONS):
            col.metric(DECISIONS[decision], decisions[decision])
        st.dataframe([{'ID': d.finding_id, 'Решение': DECISIONS[d.decision], 'Название': d.title,
                       'Серьёзность': SEVERITY.get(d.final_severity, '—'), 'Обоснование': d.audit_reason,
                       'Объединено с': d.merged_into} for d in result.audit.decisions], hide_index=True, use_container_width=True)
        st.write(result.audit.extraction_audit.status, result.audit.extraction_audit.comments)
        for item in result.audit.extraction_audit.missing_requirements:
            st.warning(f'Возможный пропуск: {item}')
        for item in result.audit.extraction_audit.unsupported_requirements:
            st.warning(f'Неподтверждённое требование: {item}')
    with methodology:
        st.markdown('''**LangGraph:** Extractor → параллельные Quality и Consistency → Auditor.

**Надёжность:** Structured Output, Pydantic, проверка цитат, повторные попытки и резервные модели.

**Кэш:** SHA-256 документа, имени и конфигурации. При изменении кода или промптов создаётся новый ключ. При сбое повторяются незавершённые узлы.

Время на панели и в PDF относится к текущему запуску (включая восстановление), а не сумме всех предыдущих попыток. Индикатор отражает этапы, а не оставшееся время.''')
        st.info('Серьёзность и уверенность AI не являются сертифицированными оценками.')
st.divider()
st.caption('AI Requirements Validator v0.7.0 | LangGraph | Google Gemini')
