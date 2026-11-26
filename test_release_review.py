"""No network. Synthetic positive/negative cases, audit evidence and replay invariants."""
from copy import deepcopy
from core.models import Requirement, ExtractionResult, AuditResult, ExtractionAudit, ConsistencyAnalysisResult
from core.release_review import detect, finalize_review


def fixture(rule=None,row=None,cap=None,change=None,extra=''):
    rule=rule or 'Месяц определяется по дате совершения покупки. В расходы входят проведенные покупки.'
    row=row or '| Покупка 30 апреля в 23:50 | Провести 1 мая в 00:10 | Майский счётчик увеличился на 100 ₸ |'
    cap=cap or 'При достижении порога отправляется push. На счёт приходится максимум один push за календарный месяц.'
    change=change or 'При изменении порога накопленная сумма сохраняется. Следующее достижение вызывает push независимо от истории предыдущей настройки.'
    texts=[rule,cap,change,row]
    reqs=[Requirement(id=f'NEW-{i}',type='functional' if i<3 else 'acceptance_criterion',text=t,source='fixture.md',paragraph=1 if i<3 else 2) for i,t in enumerate(texts)]
    sections=[dict(source='fixture.md',page=None,paragraph=1,section='Личный бюджет / Правила',text='\n'.join(texts[:3])+('\n'+extra if extra else '')),dict(source='fixture.md',page=None,paragraph=2,section='Личный бюджет / Критерии приёмки',text='| Исходные условия | Действие | Ожидаемый результат |\n| --- | --- | --- |\n'+row)]
    return ExtractionResult(requirements=reqs),sections


def codes(ex,s): return {f.explanation.split(']')[0][1:] for f in detect(ex,s)}

def main():
    ex,s=fixture()
    assert codes(ex,s)=={'EVENT_MONTH_MISMATCH','MONTHLY_QUOTA_PRIORITY'}
    print('[PASS] Two different months and quota priority, new IDs and dates')
    ex1,s1=fixture(row='| Покупка 30 апреля в 23:50 | Провести 1 мая в 00:10 | Апрельский счётчик вырос на 100 ₸ |')
    assert codes(ex1,s1)=={'MONTHLY_QUOTA_PRIORITY'}
    print('[PASS] Compatible acceptance result creates no month alert')
    ex1,s1=fixture(row='| Покупка 1 мая в 09:00 | Провести 1 мая в 09:10 | Майский счётчик вырос на 100 ₸ |')
    assert codes(ex1,s1)=={'MONTHLY_QUOTA_PRIORITY'}
    print('[PASS] Same-month case creates no month alert')
    ex1,s1=fixture(extra='Месячный лимит не сбрасывается при изменении порога.')
    assert codes(ex1,s1)=={'EVENT_MONTH_MISMATCH'}
    print('[PASS] Explicit quota priority prevents alert')
    ex1,s1=fixture(extra='Исключение для поздних проводок: месяц определяется по дате проведения.')
    assert codes(ex1,s1)=={'MONTHLY_QUOTA_PRIORITY'}
    print('[PASS] Explicit period exception prevents alert')
    ex1,s1=fixture(change='При изменении порога сохраняется сумма расходов. Следующее достижение вызывает push с учетом месячного лимита.')
    assert codes(ex1,s1)=={'EVENT_MONTH_MISMATCH'}
    print('[PASS] No unconditional quota override in source')
    ex1,s1=fixture(); s1[1]['section']='Другой функционал / Критерии приёмки'
    assert codes(ex1,s1)=={'MONTHLY_QUOTA_PRIORITY'}
    print('[PASS] Separate feature excluded')
    ex1,s1=fixture(); s1[0]['text']='Invalid source'
    try: detect(ex1,s1)
    except ValueError: pass
    else: raise AssertionError('Source check failed')
    print('[PASS] Altered source rejected')
    baseline=ConsistencyAnalysisResult(findings=[],summary='Test')
    audit=AuditResult(extraction_audit=ExtractionAudit(status='complete',missing_requirements=[],unsupported_requirements=[],comments='Synthetic'),decisions=[],readiness='ready',executive_summary='Synthetic',priority_actions=[],limitations=[])
    c,a,d=finalize_review(ex,baseline,audit,s)
    assert len(c.findings)==2 and len(a.decisions)==2 and a.readiness=='not_ready' and not baseline.findings and not audit.decisions
    c2,a2,d2=finalize_review(ex,c,a,s)
    assert len(c2.findings)==2 and c2.model_dump()==c.model_dump() and a2.model_dump()==a.model_dump() and not d2['added_review_ids']
    print('[PASS] Source-grounded output, immutable baseline, idempotent replay, readiness floor')
    print('[TEST PASSED] Release review')

if __name__=='__main__': main()
