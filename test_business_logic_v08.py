"""Offline tests: no Gemini requests, no BCC-specific rules."""
import sys
import types
from unittest.mock import patch
from core.models import Requirement, ExtractionResult, ConsistencyAnalysisResult
from core.business_logic import FormalizationResult, check_scenarios, formalize_and_check


def req(id, text):
    return Requirement(id=id,type='functional',text=text,source='synthetic.md',paragraph=1)


def op(value,kind='literal'):
    return dict(kind=kind,value=value)


def cond(left,operator,right,kind='literal'):
    return dict(left=left,op=operator,right=op(right,kind))


def rule(rid,conditions,output,interpretation):
    return dict(requirement_id=rid,conditions=conditions,output=output,interpretation=interpretation)


def var(name,kind,domain):
    return dict(name=name,kind=kind,domain=domain)


def run_model(scenario,extraction):
    return check_scenarios(FormalizationResult.model_validate({'scenarios':[scenario]}),extraction)


def main():
    dates=ExtractionResult(requirements=[req('A','Billing period follows dispatch period.'),req('B','When dispatch is in December and delivery in January, bill in January.')])
    scenario=dict(title='Billing period',scope='Same shipment',output_name='billing_period',output_kind='symbol',
        variables=[var('dispatch','symbol',['December','January']),var('delivery','symbol',['December','January'])],assumptions=[],rules=[
        rule('A',[],op('dispatch','variable'),'Billing follows dispatch'),
        rule('B',[cond('dispatch','eq','December'),cond('delivery','eq','January')],op('January'),'Specific acceptance case expects January')])
    findings=run_model(scenario,dates)
    assert len(findings)==1 and set(findings[0].requirement_ids)=={'A','B'}
    assert 'December' in findings[0].explanation and 'January' in findings[0].explanation
    assert findings[0].evidence[0].quote==dates.requirements[0].text
    print('[PASS] Different event periods produce an explicit witness and exact evidence')
    scenario['rules'][1]['output']=op('dispatch','variable')
    assert run_model(scenario,dates)==[]
    print('[PASS] Compatible temporal rules do not create findings')

    quota=ExtractionResult(requirements=[req('Q1','Deny requests after two uses per period.'),req('Q2','After a plan upgrade, allow the next request regardless of prior use.')])
    s=dict(title='Request quota',scope='Same account and period',output_name='allow_request',output_kind='boolean',
        variables=[var('prior_uses','integer',['0','1','2','3']),var('upgraded','boolean',['false','true'])],assumptions=[],rules=[
        rule('Q1',[cond('prior_uses','ge','2')],op('false'),'Deny after quota reached'),
        rule('Q2',[cond('upgraded','eq','true')],op('true'),'Allow after upgrade regardless of count')])
    assert len(run_model(s,quota))==1
    print('[PASS] Overlapping conditional rules conflict at quota boundary')
    s['rules'][0]['conditions'].append(cond('upgraded','eq','false'))
    assert run_model(s,quota)==[]
    print('[PASS] Explicit exception prevents false positive')
    s['rules'][0]['conditions'].pop()
    s['assumptions']=[cond('prior_uses','lt','2')]
    assert run_model(s,quota)==[]
    print('[PASS] Shared domain constraints exclude infeasible witnesses')
    s['assumptions']=[]
    s['rules'][1]['requirement_id']='UNKNOWN'
    try:
        run_model(s,quota)
    except ValueError:
        print('[PASS] Unknown source IDs rejected')
    else:
        raise AssertionError('Unknown ID accepted')
    s['rules'][1]['requirement_id']='Q2'
    s['rules'][1]['output']=op('prior_uses','variable')
    try:
        run_model(s,quota)
    except ValueError:
        print('[PASS] Operand type mismatch rejected')
    else:
        raise AssertionError('Type mismatch accepted')
    s['rules'][1]['output']=op('true')
    s['variables'] += [var(f'extra_{i}','integer',[str(x) for x in range(8)]) for i in range(4)]
    try:
        run_model(s,quota)
    except ValueError:
        print('[PASS] Oversized Cartesian domain rejected')
    else:
        raise AssertionError('Oversized domain accepted')
    s['variables']=s['variables'][:2]

    # Test integration with the LLM boundary without requiring keys or network.
    model=FormalizationResult.model_validate({'scenarios':[s]})
    existing=ConsistencyAnalysisResult(findings=check_scenarios(model,quota),summary='Existing')
    fake=types.ModuleType('core.llm_client')
    calls=[]
    def ask(**kwargs):
        calls.append(kwargs)
        if len(calls)==1:
            return '{"invalid":true}'
        return model.model_dump_json()
    fake.ask_gemini=ask
    with patch.dict(sys.modules,{'core.llm_client':fake}):
        result=formalize_and_check(quota,existing)
    assert len(calls)==2 and len(result.findings)==2
    assert len({f.id for f in result.findings})==2
    assert result.findings[0]==existing.findings[0]
    assert result.findings[1].id=='LOGIC-002'
    print('[PASS] Mock API correction, preserved baseline, unique IDs')
    print('[TEST PASSED] Business Logic Engine offline tests')

if __name__=='__main__':
    main()
