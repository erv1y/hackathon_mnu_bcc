import sys, types
from unittest.mock import patch
from core.models import ExtractionResult, ConsistencyAnalysisResult
from core.business_logic import (select_pairs, formalize_and_check, PairResult, SemanticResult)

x=ExtractionResult(requirements=[dict(id='A',type='business_rule',text='Billing period follows dispatch date.',source='demo.md'),dict(id='B',type='acceptance_criterion',text='Billing period follows delivery date.',source='demo.md')])
p=select_pairs(x); assert len(p)==1 and set(p[0]['requirement_ids'])=={'A','B'}
print('[PASS] Deterministic pair selection')
s=dict(title='Billing month',scope='Same shipment',output_name='month',output_kind='symbol',variables=[dict(name=k,kind='symbol',domain=['dec','jan']) for k in ['dispatch','delivery']],assumptions=[],rules=[dict(requirement_id=i,conditions=[],output=dict(kind='variable',value=k),interpretation=k) for i,k in [('A','dispatch'),('B','delivery')]])
first=PairResult(reviews=[dict(pair_id=p[0]['pair_id'],status='modeled',reason='Compare event dates',scenarios=[s])])
second=SemanticResult(reviews=[dict(pair_id=p[0]['pair_id'],decision='accept',reason='Distinct event dates',scenarios=[s])])
base=ConsistencyAnalysisResult(findings=[],summary='baseline')
module=types.ModuleType('core.llm_client'); module.ask_gemini=lambda **kw: ''
with patch.dict(sys.modules,{'core.llm_client':module}):
    with patch.object(module,'ask_gemini',side_effect=[first.model_dump_json(),second.model_dump_json()]):
        debug={}; result=formalize_and_check(x,base,debug=debug)
        assert len(result.findings)==1 and debug['complete'] and debug['verified_pair_ids']==[p[0]['pair_id']]
        assert result.findings[0].evidence[0].quote==x.requirements[0].text
    print('[PASS] Two-stage review and source-grounded witness')
    with patch.object(module,'ask_gemini',side_effect=[first.model_dump_json(),RuntimeError('offline')]):
        debug={}; result=formalize_and_check(x,base,debug=debug)
        assert not result.findings and not debug['complete'] and debug['unresolved_pair_ids']
    print('[PASS] Failed semantic review remains unresolved')
    wrong=second.model_copy(deep=True); wrong.reviews[0].scenarios[0].rules[0].requirement_id='B'
    with patch.object(module,'ask_gemini',side_effect=[first.model_dump_json(),wrong.model_dump_json(),second.model_dump_json()]):
        debug={}; result=formalize_and_check(x,base,debug=debug); assert len(result.findings)==1 and debug['passes'][0]['attempts']
    print('[PASS] Wrong pair rejected and retried')
print('[TEST PASSED] Pair Logic offline tests')
