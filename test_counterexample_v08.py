"""Offline regression tests. No credentials or network calls required."""
import copy
import json
from unittest.mock import patch
from core.models import ExtractionResult, Requirement, ConsistencyAnalysisResult
from core.counterexample import analyze_counterexamples, acceptance_expected, DirectResult, validate_reviews
from core.business_logic import select_pairs


def fixture():
    a = Requirement(id='A',type='business_rule',text='Billing period is determined by dispatch date.',source='shipping.md',paragraph=1)
    b = Requirement(id='B',type='acceptance_criterion',text='| Dispatch in March | Deliver in April | April billing period |',source='shipping.md',paragraph=2)
    sections=[dict(source=a.source,page=None,paragraph=1,text=a.text,section='Rules'),
              dict(source=b.source,page=None,paragraph=2,text='| Initial state | Action | Expected result |\n| --- | --- | --- |\n'+b.text,section='Acceptance')]
    extraction=ExtractionResult(requirements=[a,b])
    return extraction,sections


def reply(payload, verdict='contradiction'):
    p=json.loads(payload)['pairs'][0]
    a,b=p['requirements']
    return {'reviews':[dict(pair_id=p['pair_id'],verdict=verdict,scope='Один груз',scenario='Отправка в марте, доставка в апреле',
                a=dict(requirement_id=a['id'],quote=a['text'],expected_result='March billing period'),
                b=dict(requirement_id=b['id'],quote=b['text'],expected_result='April billing period'),
                rationale='Правила дают разные месяцы для одной отправки.',title='Разные расчётные периоды',recommendation='Согласовать период.') ]}


def main():
    e, sections=fixture()
    base=ConsistencyAnalysisResult(findings=[],summary='Baseline')
    pairs=select_pairs(e,sections)
    assert len(pairs)==1 and pairs==select_pairs(e,sections)
    print('[PASS] Existing deterministic pair selection')
    assert acceptance_expected(e.requirements[1],sections)=='April billing period'
    assert acceptance_expected(e.requirements[1],[]) is None
    print('[PASS] Exact acceptance cell lock, conservative fallback')
    debug={}
    with patch('core.llm_client.ask_gemini',side_effect=lambda **kw: json.dumps(reply(kw['prompt']))) as mock:
        result=analyze_counterexamples(e,base,sections,debug)
    assert mock.call_count==1 and len(result.findings)==1 and debug['complete']
    f=result.findings[0]
    assert f.id=='LOGIC-001' and f.category=='contradiction' and 'April billing period' in f.explanation
    assert f.evidence[1].quote==e.requirements[1].text
    print('[PASS] One-stage candidate, exact evidence, diagnostics')
    def wrong_then_correct(**kw):
        r=reply(kw['prompt'])
        if 'validation_error' not in json.loads(kw['prompt']):
            r['reviews'][0]['b']['expected_result']='March billing period'
        return json.dumps(r)
    with patch('core.llm_client.ask_gemini',side_effect=wrong_then_correct) as mock:
        result=analyze_counterexamples(e,result,sections,debug)
    assert mock.call_count==2 and len(result.findings)==2 and result.findings[-1].id=='LOGIC-002'
    assert 'locked cell' in debug['passes'][0]['attempts'][0]['error']
    print('[PASS] Altered acceptance result rejected, retry, baseline preserved, unique IDs')
    def bad_quote(**kw):
        r=reply(kw['prompt']); r['reviews'][0]['a']['quote']='fabricated quote'; return json.dumps(r)
    with patch('core.llm_client.ask_gemini',side_effect=bad_quote) as mock:
        invalid=analyze_counterexamples(e,base,sections,debug)
    assert mock.call_count==2 and not invalid.findings and not debug['complete'] and debug['unresolved_pair_ids']==['PAIR-001']
    print('[PASS] Fabricated quote fails closed')
    with patch('core.llm_client.ask_gemini',side_effect=RuntimeError('simulated provider failure')):
        invalid=analyze_counterexamples(e,result,sections,debug)
    assert len(invalid.findings)==2 and debug['passes'][0]['status']=='api_failed' and not debug['complete']
    print('[PASS] API failure preserves baseline and marks unresolved')
    def status_reply(verdict):
        return lambda **kw: json.dumps(reply(kw['prompt'],verdict))
    for verdict, count, complete in [('priority_ambiguity',1,True),('compatible',0,True),('not_comparable',0,True),('unresolved',0,False)]:
        with patch('core.llm_client.ask_gemini',side_effect=status_reply(verdict)):
            r=analyze_counterexamples(e,base,sections,debug)
        assert len(r.findings)==count and debug['complete']==complete
        if count: assert r.findings[0].severity=='medium'
    print('[PASS] Distinct verdicts, unresolved not counted as reviewed')
    payload=json.dumps({'pairs':[dict(pairs[0],requirements=[x.model_dump() for x in e.requirements])]})
    r=reply(payload); r['reviews'][0]['a']['requirement_id']='OTHER'
    try:
        validate_reviews(DirectResult.model_validate(r),pairs,{x.id:x for x in e.requirements},{'B':'April billing period'})
    except ValueError: pass
    else: raise AssertionError('Wrong requirement ID accepted')
    print('[PASS] Wrong requirement IDs rejected')
    print('[TEST PASSED] Direct Counterexample offline tests')

if __name__=='__main__': main()
