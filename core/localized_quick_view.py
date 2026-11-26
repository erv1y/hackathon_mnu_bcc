from core.quick_view import preliminary_graph
from core.i18n import tr, localize_graph

def show_quick_result(result, pdf_bytes, events, lang="kk"):
    def t(key):
        return tr(key, lang)
    severity = {k: t(v) for k, v in {'critical':'Критический','high':'Высокий','medium':'Средний','low':'Низкий'}.items()}
    types = {k: t(v) for k, v in {'functional':'Функциональное','non_functional':'Нефункциональное','business_rule':'Бизнес-правило','acceptance_criterion':'Критерий приёмки','other':'Прочее'}.items()}
    import streamlit as st
    import streamlit.components.v1 as components
    from core.knowledge_graph import RELATIONS, render_graph_html, graph_json
    st.header(t('2. Quick Validation — предварительный результат'))
    st.warning(t('Независимый аудит не выполнялся. Готовность к разработке не оценена отдельным аудитором.'))
    if 'Cache Hit' in events:
        st.success(t('Cache Hit — без новых AI-запросов.'))
    elif 'Resume' in events:
        st.info(t('Resume — восстановление сохранённого состояния.'))
    st.caption(t('Документы: ')+', '.join(result.source_files))
    for col, label, value in zip(st.columns(4), [t('Требований'),t('Замечаний качества'),t('Замечаний согласованности'),t('Время запуска')], [len(result.extraction.requirements), sum(len(a.findings) for a in result.quality.assessments),len(result.consistency.findings), (f'{result.execution_time_seconds:.2f}' + t(' сек.'))]):
        col.metric(label,value)
    st.caption(t('Замечания качества и согласованности могут описывать один дефект.'))
    if pdf_bytes:
        st.download_button(t('Скачать предварительный PDF'),pdf_bytes,'quick_preliminary_report.pdf','application/pdf')
    st.download_button(t('Скачать Quick JSON'),result.model_dump_json(indent=2),'quick_validation.json','application/json')
    quality, consistency, register, graph_tab = st.tabs([t('Quality'),t('Consistency'),t('Requirements'),t('Knowledge Graph')])
    with quality:
        for a in result.quality.assessments:
            with st.expander(f'{a.requirement_id} | {a.quality_score}/100 | {t(a.status)}'):
                for f in a.findings:
                    st.write(severity[f.severity], t(f.criterion), f.explanation)
                    st.code(f.evidence,language=None)
                    st.write(f.recommendation)
    with consistency:
        for f in result.consistency.findings:
            with st.expander(f"{f.id} | {severity[f.severity]} | {f.title}"):
                st.write(f.explanation)
                for e in f.evidence:
                    st.caption(f'{e.requirement_id} | {e.source}')
                    st.code(e.quote,language=None)
                st.write(f.recommendation)
    with register:
        st.dataframe([{'ID':r.id,t('Тип'):types[r.type],t('Текст'):r.text,t('Источник'):r.source,t('Страница'):r.page,t('Абзац'):r.paragraph} for r in result.extraction.requirements],hide_index=True)
    with graph_tab:
        st.caption(t('Предварительные связи — не проверены отдельным аудитором.'))
        cats=st.multiselect(t('Типы связей'),list(RELATIONS),default=list(RELATIONS),format_func=lambda x:t(RELATIONS[x][0]),key='quick_rel')
        levels=['critical','high','medium','low']
        sev=st.multiselect(t('Серьёзность'),levels,default=levels,key='quick_sev',format_func=lambda x:severity[x])
        connected=st.checkbox(t('Только связанные требования'),value=True,key='quick_connected')
        graph=preliminary_graph(result,cats,sev,connected)
        if graph.number_of_nodes():
            components.html(render_graph_html(localize_graph(graph, lang)),height=650,scrolling=True)
            st.download_button(t('Скачать предварительный граф'),graph_json(graph),'quick_graph.json','application/json')
        else:
            st.info(t('Нет связей по выбранным фильтрам.'))
