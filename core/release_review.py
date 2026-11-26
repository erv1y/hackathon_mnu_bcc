"""Bounded, deterministic post-audit checks; NOT a general natural-language solver.
No API calls, no dependency on previous AI verdicts, no fixed requirement IDs.
"""
import re
from collections import Counter
from core.models import AuditDecision, ConsistencyFinding, FindingEvidence

MONTHS = ('январь январе января январский', 'февраль феврале февраля февральский',
          'март марте марта мартовский', 'апрель апреле апреля апрельский',
          'май мае мая майский', 'июнь июне июня июньский', 'июль июле июля июльский',
          'август августе августа августовский', 'сентябрь сентябре сентября сентябрьский',
          'октябрь октябре октября октябрьский', 'ноябрь ноябре ноября ноябрьский',
          'декабрь декабре декабря декабрьский')
MONTH_LOOKUP = {w: words.split()[0] for words in MONTHS for w in words.split()}
MONTH_WORD = '(?:'+'|'.join(MONTH_LOOKUP)+')'
LIMITATION = ('Дополнительная программная проверка охватывает только явно распознаваемые '
              'русскоязычные правила учёта покупок по месяцам и месячного лимита уведомлений. '
              'Это кандидаты для проверки аналитиком, а не доказательство полноты или общего качества AI.')
GROUP_NOTE = ('При объединении сохранены также цитаты участников группы; '
              'подлинность цитат проверена, их смысловая релевантность требует проверки.')

def norm(s): return ' '.join(s.split()).casefold().replace('ё','е')

def ev(r):
    return FindingEvidence(requirement_id=r.id, quote=r.text, source=r.source,
                           page=r.page, paragraph=r.paragraph)

def check_source(extraction, sections):
    ids = [r.id for r in extraction.requirements]
    if not ids or len(ids) != len(set(ids)): raise ValueError('Empty or duplicate source registry')
    for r in extraction.requirements:
        # Preserve original spelling when validating source, normalize whitespace only.
        if not any((s['source'],s.get('page'),s.get('paragraph'))==(r.source,r.page,r.paragraph)
                   and ' '.join(r.text.split()) in ' '.join(s['text'].split()) for s in sections):
            raise ValueError(f'Source mismatch: {r.id}')

def detect(extraction, sections):
    check_source(extraction, sections)
    reqs=extraction.requirements
    ctx={(s['source'],s.get('page'),s.get('paragraph')):s.get('section','') for s in sections}
    def parts(r): return ctx.get((r.source,r.page,r.paragraph),'').split(' / ')
    def scope(a,b):
        x,y=parts(a),parts(b)
        # A named shared feature is required. Do not compare unstructured PDF pages.
        return a.source==b.source and bool(x[0]) and x[0]==y[0] and (len(x)==1 or len(y)==1 or x[1]==y[1] or not any(re.match(r'часть\s|part\s',z,re.I) for z in (x[1],y[1])))
    def relevant_context(a):
        return '\n'.join(norm(s['text']) for s in sections if s['source']==a.source and s.get('section','').split(' / ')[0]==parts(a)[0])
    out=[]; seen=set()
    def add(code,a,b,title,explanation,recommendation,severity):
        key=(code,tuple(sorted((a.id,b.id))))
        if key in seen: return
        seen.add(key)
        out.append(ConsistencyFinding(id='REVIEW-PENDING',category='inconsistent_constraint',severity=severity,
            requirement_ids=[a.id,b.id],title=title,evidence=[ev(a),ev(b)],
            explanation=f'[{code}] {explanation} Кандидат программной проверки; подтвердить область и исключения вручную.',
            impact='Без уточнения один сценарий может иметь разные ожидаемые результаты.',recommendation=recommendation,confidence=.75))
    for a in reqs:
        t=norm(a.text)
        if a.type=='acceptance_criterion': continue
        # Intentionally narrow: explicit, unqualified purchase-date rule plus 3-cell acceptance example.
        if re.search(r'\bмесяц\s+(?:учета\s+)?определяется\s+по\s+дате\s+совершения\s+покупки\b',t):
            context=relevant_context(a)
            if re.search(r'исключени\w*\s+(?:для|при)\s+(?:поздн|отлож|провод|покуп)|если[^.]{0,100}проведен\w*[^.]{0,100}(?:следующ\w* месяц|дат\w* проведен)|месяц[^.]{0,80}по дате проведен',context): continue
            for b in reqs:
                if b.type!='acceptance_criterion' or not scope(a,b): continue
                from core.counterexample import acceptance_expected, cells
                result=acceptance_expected(b,sections)
                if result is None: continue
                c=cells(b.text)
                # Keep input event and action distinct; don't infer from month mentions alone.
                m=re.search(r'\bпокупка\s+\d{1,2}\s+('+MONTH_WORD+r')\b',norm(c[0]))
                n=re.search(r'\bпровести\s+\d{1,2}\s+('+MONTH_WORD+r')\b',norm(c[1]))
                z=re.match(r'('+MONTH_WORD+r')\s+счетчик\s+(?:вырос|увеличил\w*)\b',norm(result))
                if m and n and z:
                    first,post,expected=(MONTH_LOOKUP[x.group(1)] for x in (m,n,z))
                    if first!=post and expected==post:
                        add('EVENT_MONTH_MISMATCH',a,b,'Разные месяцы учёта одной покупки',
                            f'Дата совершения относится к месяцу «{first}», дата проведения — «{post}». Правило выбирает «{first}», критерий приёмки — «{expected}».',
                            'Согласовать дату, определяющую месяц учёта, и исправить правило либо критерий приёмки.', 'high')
        # A threshold change plus an absolute per-entity monthly cap is a priority question, not proof of contradiction.
        cap=re.search(r'\bна\s+(карту|счет|клиента)\s+приходится\s+максимум\s+один\s+(?:такой\s+)?(push|пуш)\s+за\s+календарный\s+месяц\b',t)
        if cap and re.search(r'\bпорог\w*\b',t):
            for b in reqs:
                if a.id==b.id or b.type=='acceptance_criterion' or not scope(a,b): continue
                # Require shared functional block, not generic notifications across separate operations.
                if (a.source,a.page,a.paragraph)!=(b.source,b.page,b.paragraph): continue
                u=norm(b.text)
                if not re.search(r'при\s+изменении\s+порога',u) or not re.search(r'следующее\s+достижение\s+вызывает\s+'+cap[2]+r'\s+независимо\s+от\s+истории\s+предыдущей\s+настройки',u): continue
                context=relevant_context(a)
                if re.search(r'(?:месячн\w* лимит|лимит[^.]{0,40}месяц)[^.]{0,80}(?:сбрасывается|не действует|сохраняется|имеет приоритет|не сбрасывается)|(?:исключение из|не отменяет)\s+[^.]{0,30}(?:лимит|ограничен)',context): continue
                add('MONTHLY_QUOTA_PRIORITY',a,b,'Не определён приоритет месячного лимита и смены порога',
                    'Гипотетический сценарий: уведомления включены; на том же объекте уже отправлен один push в этом месяце, затем порог повышен выше накопленных расходов и достигнут новой покупкой в том же месяце. Месячный лимит запрещает второй push; правило изменения порога требует push независимо от истории настройки. Явный приоритет не установлен в распознанных правилах.',
                    'Уточнить, сохраняется ли месячный лимит при изменении порога; добавить тест повторного достижения в том же месяце.', 'medium')
    return out

def finalize_review(extraction, consistency, audit, sections):
    """Copy inputs; preserve evidence from accepted merge members; add distinct review alerts.
    Does not rewrite previous AI verdicts except evidence-only revisions and readiness.
    """
    check_source(extraction,sections)
    con=consistency.model_copy(deep=True); result=audit.model_copy(deep=True)
    reqs={r.id:r for r in extraction.requirements}
    decisions={d.finding_id:d for d in result.decisions}
    if len(decisions)!=len(result.decisions): raise ValueError('Duplicate audit IDs')
    from collections import defaultdict
    groups=defaultdict(list)
    for d in result.decisions:
        if d.decision=='merged':
            target=decisions.get(d.merged_into)
            if not target or target.decision not in ('confirmed','revised'): raise ValueError('Invalid merge target')
            if (norm(d.root_cause),norm(d.scope))!=(norm(target.root_cause),norm(target.scope)): raise ValueError('Merge scope mismatch')
            groups[target.finding_id].append(d)
        for e in d.final_evidence:
            r=reqs.get(e.requirement_id)
            if not r or not e.quote.strip() or ' '.join(e.quote.split()) not in ' '.join(r.text.split()) or (e.source,e.page,e.paragraph)!=(r.source,r.page,r.paragraph): raise ValueError('Invalid audit evidence')
    added_evidence=[]
    for fid,members in groups.items():
        d=decisions[fid]
        known={(e.requirement_id,e.quote) for e in d.final_evidence}
        for m in members:
            for e in m.final_evidence:
                if (e.requirement_id,e.quote) not in known:
                    d.final_evidence.append(e.model_copy(deep=True)); known.add((e.requirement_id,e.quote))
                    if e.requirement_id not in d.requirement_ids: d.requirement_ids.append(e.requirement_id)
                    added_evidence.append({'representative':fid,'member':m.finding_id,'requirement_id':e.requirement_id})
                    d.decision='revised'
        if any(x['representative']==fid for x in added_evidence):
            d.audit_reason += ' '+GROUP_NOTE
    if added_evidence and GROUP_NOTE not in result.limitations: result.limitations.append(GROUP_NOTE)
    # Keep the review alerts separate from AI findings, avoiding an unvalidated semantic merge.
    existing={(f.explanation.split(']')[0],tuple(sorted(f.requirement_ids))) for f in con.findings if f.id.startswith('REVIEW-')}
    used=set(decisions)|{f.id for f in con.findings}; new=[]
    for f in detect(extraction,sections):
        key=(f.explanation.split(']')[0],tuple(sorted(f.requirement_ids)))
        if key in existing: continue
        n=1
        while f'REVIEW-{n:03d}' in used: n+=1
        f.id=f'REVIEW-{n:03d}'; used.add(f.id); con.findings.append(f); new.append(f.id)
        result.decisions.append(AuditDecision(finding_id=f.id,decision='revised',final_severity=f.severity,
            requirement_ids=f.requirement_ids,title=f.title,explanation=f.explanation,
            recommendation=f.recommendation,audit_reason='Добавлено после AI-аудита ограниченной программной проверкой; требует ручного подтверждения.',
            confidence=f.confidence,root_cause=f.title,scope='Один объект и функциональный процесс',final_evidence=f.evidence))
        if f.recommendation not in result.priority_actions: result.priority_actions.append(f.recommendation)
    if new: con.summary+=f' | Дополнительная программная проверка: {len(new)} кандидатов для ручного подтверждения.'
    if LIMITATION not in result.limitations: result.limitations.append(LIMITATION)
    retained=[d for d in result.decisions if d.decision in ('confirmed','revised')]
    counts=Counter(d.final_severity for d in retained); statuses=Counter(d.decision for d in result.decisions)
    if counts['high'] or counts['critical']: result.readiness='not_ready'
    elif retained and result.readiness=='ready': result.readiness='conditionally_ready'
    result.executive_summary=(f'Статус: {result.readiness}. Сохранено замечаний: {len(retained)} '
        f'(critical: {counts["critical"]}, high: {counts["high"]}, medium: {counts["medium"]}, low: {counts["low"]}). '
        f'Объединено: {statuses["merged"]}; отклонено: {statuses["rejected"]}. '
        f'Включены {sum(d.finding_id.startswith("REVIEW-") for d in retained)} программных кандидатов для ручного подтверждения. Полнота не гарантирована.')
    return con,result,{'added_review_ids':new,'restored_group_evidence':added_evidence,'limitations':LIMITATION}
