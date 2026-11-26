"""Offline integration tests for analyst output contracts; no Gemini calls."""
import json
from unittest.mock import patch
from core.models import ExtractionResult, Requirement
from agents import quality_analyst as q, consistency_analyst as c

extraction = ExtractionResult(requirements=[
    Requirement(id='R-1', type='business_rule', text='Maximum 10 items.', source='demo.md', paragraph=1),
    Requirement(id='R-2', type='business_rule', text='Maximum 20 items.', source='demo.md', paragraph=2)])
sections = [{'source':r.source,'page':r.page,'paragraph':r.paragraph,'text':r.text} for r in extraction.requirements]
quality_good = {'assessments':[
    {'requirement_id':'R-1','status':'needs_clarification','quality_score':65,'findings':[
        {'requirement_id':'R-1','criterion':'clarity','severity':'medium','explanation':'Уточнить область.',
         'recommendation':'Согласовать область.','confidence':0.8}]},
    {'requirement_id':'R-2','status':'passed','quality_score':95,'findings':[]}]}
quality_bad = json.loads(json.dumps(quality_good)); quality_bad['assessments'][0]['requirement_id']='UNKNOWN'
with patch.object(q,'ask_gemini',side_effect=[json.dumps(quality_bad),json.dumps(quality_good)]) as api:
    quality = q.analyze_quality(extraction, sections)
    assert api.call_count == 2
    assert json.loads(api.call_args.kwargs['prompt'])['source_context'] == sections
    assert quality.assessments[0].findings[0].evidence == extraction.requirements[0].text
    q.validate_quality(quality, extraction)
print('[PASS] Quality coverage, retry, and verbatim evidence')

finding = {'category':'contradiction','severity':'high','requirement_ids':['R-1','R-2'],
           'title':'Конфликт лимитов','explanation':'Разные лимиты при одинаковых условиях.',
           'impact':'Неоднозначная реализация.','recommendation':'Согласовать лимит.','confidence':0.9}
good = {'findings':[finding],'summary':'Тест'}
bad = {'findings':[{**finding,'requirement_ids':['R-1','UNKNOWN']}],'summary':'Тест'}
with patch.object(c,'ask_gemini',side_effect=[json.dumps(bad),json.dumps(good),json.dumps(good)]) as api:
    consistency = c.analyze_consistency(extraction, sections)
    assert api.call_count == 3
    assert len(consistency.findings) == 1
    assert consistency.findings[0].id == 'CON-001'
    assert consistency.findings[0].evidence[1].quote == extraction.requirements[1].text
    c.validate_findings(consistency, extraction)
    assert json.loads(api.call_args.kwargs['prompt'])['pass']=='scenario'
print('[PASS] Two passes, retry, duplicate suppression and source evidence')

invalid = consistency.model_copy(deep=True)
invalid.findings[0].evidence[0].quote = 'invented'
try:
    c.validate_findings(invalid, extraction)
except ValueError:
    print('[PASS] Existing strict validator rejects fabricated quotes')
else:
    raise AssertionError('Unsupported quote accepted')

with patch.object(q,'ask_gemini',return_value=json.dumps(quality_bad)):
    try:
        q.analyze_quality(extraction, sections)
    except RuntimeError:
        print('[PASS] Invalid quality output fails closed')
    else:
        raise AssertionError('Invalid quality accepted')
print('[TEST PASSED] Analysts 2.0 offline tests')
