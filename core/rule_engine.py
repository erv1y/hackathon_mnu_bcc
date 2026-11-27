"""Conservative, source-grounded checks. No external calls."""
import re
from itertools import combinations
from core.models import ConsistencyFinding, ConsistencyAnalysisResult, FindingEvidence


def evidence(r):
    return FindingEvidence(requirement_id=r.id, quote=r.text, source=r.source,
                           page=r.page, paragraph=r.paragraph)


def check_rules(extraction, sections=None):
    findings = []
    def add(rule, refs, title, explanation, impact, recommendation, severity='high'):
        findings.append(ConsistencyFinding(id=f'RULE-{len(findings)+1:03d}',
            category='inconsistent_constraint', severity=severity,
            requirement_ids=[r.id for r in refs], title=title,
            evidence=[evidence(r) for r in refs], explanation=f'[{rule}] {explanation}',
            impact=impact, recommendation=recommendation, confidence=0.85))
    # Only compare explicit HTTP method and explicit write obligation within
    # the same source and same endpoint section. Never infer from URL alone.
    contexts = {(s['source'], s.get('page'), s.get('paragraph')): s.get('section', '')
                for s in (sections or [])}
    def scope(r):
        heading = contexts.get((r.source,r.page,r.paragraph),'')
        parts = heading.split(' / ')
        # Match only a clearly named REST API parent, not arbitrary documents.
        for i, part in enumerate(parts):
            if re.search(r'\bREST API\b', part, re.I):
                return (r.source, ' / '.join(parts[:i+1]))
        return None
    reqs = extraction.requirements
    for method in reqs:
        if not re.search(r'\|\s*\**(?:REST type|HTTP method|HTTP-метод)\**\s*\|\s*`?GET`?\s*\|',method.text,re.I):
            continue
        group = scope(method)
        if not group:
            continue
        # Multiple methods in a section make endpoint association ambiguous.
        method_rows = [r for r in reqs if scope(r)==group and re.search(r'\|\s*\**(?:REST type|HTTP method|HTTP-метод)\**\s*\|',r.text,re.I)]
        if len(method_rows)!=1:
            continue
        writes = [r for r in reqs if scope(r)==group and re.search(r'(?:^|\n)\s*(?:\d+\.\s*)?(?:заменить|сохранить|удалить|создать|обновить|replace|save|delete|create|update)\s+(?:настройк\w*|запис\w*|данные|ресурс\w*|(?:the\s+)?(?:settings|record|resource|data))\b',r.text,re.I)]
        if writes:
            add('HTTP_SAFE_METHOD', [method,writes[0]], 'GET и изменение состояния',
                'В одном разделе API указан GET и явное изменение состояния. Требуется подтвердить, что они относятся к одной операции.',
                'Риск непреднамеренного выполнения записи и некорректного поведения HTTP-клиентов.',
                'Проверить принадлежность к одному эндпоинту; для записи выбрать подходящий метод и уточнить контракт.')
    # Compare explicit numeric intervals only for identical named subjects and scope.
    # No fuzzy subject matching or comparison of unrelated limits.
    pattern = re.compile(r'^\s*(?:[-*]\s*)?([\w -]+?)\s*:\s*(\d+)\s*(?:–|—|\.\.)\s*(\d+)\s*([\w%₸$]+)\s*$',re.I)
    intervals = []
    for r in reqs:
        m = pattern.fullmatch(r.text)
        if m and r.paragraph is not None and int(m[2])<=int(m[3]):
            intervals.append((r,m[1].strip().casefold(),int(m[2]),int(m[3]),m[4].casefold()))
    for (a,sa,lo,hi,u),(b,sb,l2,h2,u2) in combinations(intervals,2):
        if (a.source,a.page,a.paragraph,sa,u)==(b.source,b.page,b.paragraph,sb,u2) and max(lo,l2)>min(hi,h2):
            add('DISJOINT_INTERVALS',[a,b],'Непересекающиеся диапазоны',
                'Одноимённые ограничения в одном блоке задают непересекающиеся диапазоны.',
                'Невозможно выбрать значение, удовлетворяющее обоим диапазонам одновременно.',
                'Уточнить условия применения и согласовать диапазон.')
    return findings


def augment_consistency(extraction, consistency, sections=None):
    rules = check_rules(extraction, sections)
    used = {f.id for f in consistency.findings}
    for f in rules:
        n=1
        while f'RULE-{n:03d}' in used:
            n+=1
        f.id=f'RULE-{n:03d}'
        used.add(f.id)
    print(f'[RULE ENGINE] {len(rules)} additional candidates')
    return ConsistencyAnalysisResult(repair_diagnostics=consistency.repair_diagnostics, unresolved=consistency.unresolved,findings=[*consistency.findings,*rules],
        summary=consistency.summary+f' | Программные проверки: {len(rules)} дополнительных кандидатов до аудита.')


def main():
    import argparse
    from pathlib import Path
    from core.document_reader import read_document
    from core.models import ExtractionResult
    from agents.consistency_analyst import validate_findings
    parser = argparse.ArgumentParser(description='Run offline rules on an existing extraction')
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--document', type=Path, nargs='+', required=True)
    parser.add_argument('--consistency', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    extraction = ExtractionResult.model_validate_json(args.input.read_text(encoding='utf-8'))
    sections = [s for p in args.document for s in read_document(p)]
    for r in extraction.requirements:
        if not any(s['source']==r.source and s.get('page')==r.page and s.get('paragraph')==r.paragraph and ' '.join(r.text.split()) in ' '.join(s['text'].split()) for s in sections):
            raise ValueError(f'Document does not match extraction: {r.id}')
    baseline = ConsistencyAnalysisResult.model_validate_json(args.consistency.read_text(encoding='utf-8')) if args.consistency else ConsistencyAnalysisResult(findings=[],summary='Автономная программная проверка.')
    validate_findings(baseline, extraction)
    result = augment_consistency(extraction, baseline, sections)
    validate_findings(result, extraction)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(result.model_dump_json(indent=2),encoding='utf-8')
    for f in result.findings:
        print(f'{f.id} [{f.severity}] {f.title}: {", ".join(f.requirement_ids)}')
    print(f'[SAVED] {args.output}')


if __name__ == '__main__':
    main()
