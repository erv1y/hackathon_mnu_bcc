"""Build a local, evidence-linked graph from an existing validation result."""
from html import escape
import json
import networkx as nx

TYPES = {
    'functional': ('Функциональное', '#16836c'),
    'non_functional': ('Нефункциональное', '#d97706'),
    'business_rule': ('Бизнес-правило', '#9333ea'),
    'acceptance_criterion': ('Критерий приёмки', '#64748b'),
    'other': ('Прочее', '#64748b'),
}
RELATIONS = {
    'contradiction': ('Противоречие', '#dc2626'),
    'duplicate': ('Дублирование', '#2563eb'),
    'inconsistent_constraint': ('Несогласованное ограничение', '#ea580c'),
    'missing_requirement': ('Возможный пропуск', '#64748b'),
    'missing_dependency': ('Отсутствующая зависимость', '#9333ea'),
    'scope_conflict': ('Конфликт области', '#be123c'),
    'terminology_inconsistency': ('Терминология', '#0891b2'),
}
PAIR_CATEGORIES = {'contradiction', 'duplicate', 'inconsistent_constraint', 'scope_conflict', 'terminology_inconsistency'}


def build_knowledge_graph(result, categories=None, severities=None, only_connected=False):
    graph = nx.MultiGraph()
    known = {r.id: r for r in result.extraction.requirements}
    decisions = {d.finding_id: d for d in result.audit.decisions}
    for rid, req in known.items():
        label, color = TYPES.get(req.type, TYPES['other'])
        graph.add_node('req:' + rid, kind='requirement', label=rid,
                       color=color, type=label, text=req.text, source=req.source,
                       page=req.page, paragraph=req.paragraph)
    for finding in result.consistency.findings:
        decision = decisions.get(finding.id)
        if not decision or decision.decision not in ('confirmed', 'revised'):
            continue
        if categories is not None and finding.category not in categories:
            continue
        if severities is not None and decision.final_severity not in severities:
            continue
        ids = list(dict.fromkeys(decision.requirement_ids))
        if not ids or any(rid not in known for rid in ids):
            continue
        label, color = RELATIONS.get(finding.category, (finding.category, '#64748b'))
        evidence = [e.model_dump(mode='json') for e in (decision.final_evidence or finding.evidence) if e.requirement_id in ids]
        attrs = dict(finding_id=finding.id, category=finding.category,
                     label=label, color=color, severity=decision.final_severity,
                     title=decision.title, explanation=decision.explanation,
                     recommendation=decision.recommendation, evidence=evidence,
                     decision=decision.decision, audit_reason=decision.audit_reason)
        # A direct edge is safe only if the two endpoints are unchanged.
        if (len(ids) == 2 and set(ids) == set(finding.requirement_ids)
                and finding.category in PAIR_CATEGORIES):
            graph.add_edge('req:' + ids[0], 'req:' + ids[1], **attrs)
        else:
            # A finding node represents a group or missing item, not invented pairwise links.
            issue_id = 'issue:' + finding.id
            graph.add_node(issue_id, kind='finding', **attrs)
            for rid in ids:
                graph.add_edge(issue_id, 'req:' + rid, **attrs)
    if only_connected:
        graph.remove_nodes_from(list(nx.isolates(graph)))
    return graph


def graph_json(graph):
    return json.dumps({'nodes': [dict(id=n, **a) for n, a in graph.nodes(data=True)],
                       'edges': [dict(source=u, target=v, key=k, **a)
                                 for u, v, k, a in graph.edges(keys=True, data=True)]},
                      ensure_ascii=False, indent=2)


def render_graph_html(graph):
    from pyvis.network import Network
    net = Network(height='620px', width='100%', bgcolor='#f8fafc',
                  font_color='#123b35', directed=False, cdn_resources='in_line')
    # Use numeric internal identifiers and escape all document-derived strings.
    mapping = {node: index for index, node in enumerate(graph.nodes)}
    def safe(value):
        return escape(str(value), quote=True)
    def tooltip(data):
        lines = [data.get('label', ''), data.get('title', ''), data.get('type', ''),
                 data.get('text', ''), data.get('source', ''),
                 'Страница: ' + str(data['page']) if data.get('page') is not None else '',
                 'Абзац: ' + str(data['paragraph']) if data.get('paragraph') is not None else '',
                 data.get('severity', ''), data.get('explanation', ''),
                 data.get('recommendation', '')]
        return '<br>'.join(safe(line) for line in lines if line)
    for node, data in graph.nodes(data=True):
        issue = data['kind'] == 'finding'
        net.add_node(mapping[node], label=safe(data['label'] if not issue else data['finding_id']),
                     title=tooltip(data), color=data['color'], shape='diamond' if issue else 'dot',
                     size=20 if issue else 16)
    for u, v, key, data in graph.edges(keys=True, data=True):
        net.add_edge(mapping[u], mapping[v], label=safe(data['label']),
                     title=tooltip(data), color=data['color'], width=2,
                     dashes=data['category'] == 'duplicate')
    net.set_options(json.dumps({'interaction': {'hover': True, 'navigationButtons': True},
                                'physics': {'stabilization': {'iterations': 150}},
                                'edges': {'smooth': {'enabled': True, 'type': 'dynamic'}},
                                'nodes': {'font': {'size': 14}}}))
    return net.generate_html(notebook=False)
