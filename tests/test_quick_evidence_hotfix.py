import copy
import json
import unittest
from unittest.mock import patch
from google import genai
from google.genai import _transformers
from agents import quick_validator as q
from core.llm_client import GeminiRequestError

SECTIONS = [{'source':'fixture.md','paragraph':1,'text':'Maximum transfer is 100000 KZT.'},
            {'source':'fixture.md','paragraph':2,'text':'Maximum transfer is 500000 KZT.'}]

def fixture():
    return {'extraction':{'requirements':[
        {'id':'FR-1','type':'business_rule','text':SECTIONS[0]['text'],'source':'fixture.md','paragraph':1},
        {'id':'FR-2','type':'business_rule','text':SECTIONS[1]['text'],'source':'fixture.md','paragraph':2}]},
        'quality':{'assessments':[
            {'requirement_id':'FR-1','status':'needs_clarification','quality_score':70,'findings':[
                {'criterion':'clarity','severity':'medium','explanation':'The transfer period is not defined.',
                 'recommendation':'Define the applicable period.','confidence':.8}]},
            {'requirement_id':'FR-2','status':'passed','quality_score':100,'findings':[]}]},
        'consistency':{'summary':'A preliminary conflict was found.','findings':[
            {'category':'contradiction','severity':'high','requirement_ids':['FR-1','FR-2'],
             'title':'Different maximum transfer values','explanation':'Both transfer limits differ.',
             'impact':'Unclear implementation','recommendation':'Resolve the applicable limit.','confidence':.9}]}}

class QuickEvidenceHotfix(unittest.TestCase):
    def run_response(self,*responses):
        with patch.object(q,'ask_gemini',side_effect=[json.dumps(r) if isinstance(r,dict) else r for r in responses]) as model:
            result=q.quick_validate(SECTIONS)
        return result,model

    def test_both_provider_schemas_and_no_redundant_fields(self):
        with genai.Client(api_key='offline-schema-test',vertexai=False) as client:
            for cls in (q.QuickAgentResponse,q.QuickRepairResponse):
                _transformers.t_schema(client._api_client,cls)
        self.assertNotIn('evidence',q.QuickQualityFinding.model_fields)
        self.assertNotIn('requirement_id',q.QuickQualityFinding.model_fields)
        self.assertNotIn('evidence',q.QuickConsistencyFinding.model_fields)

    def test_complete_in_one_call_with_verified_evidence(self):
        result,model=self.run_response(fixture())
        self.assertEqual(model.call_count,1)
        self.assertEqual(result.quality.assessments[0].findings[0].evidence,SECTIONS[0]['text'])
        self.assertEqual(result.quality.assessments[0].findings[0].requirement_id,'FR-1')
        self.assertEqual([e.quote for e in result.consistency.findings[0].evidence],[s['text'] for s in SECTIONS])
        self.assertEqual([e.paragraph for e in result.consistency.findings[0].evidence],[1,2])
        self.assertEqual(q.QuickValidationResult.model_validate_json(result.model_dump_json()),result)

    def test_only_missing_quality_and_locked_consistency(self):
        good=fixture(); first=copy.deepcopy(good); first['quality']['assessments'].pop()
        repair={'quality':{'assessments':[good['quality']['assessments'][1]]},'consistency':None}
        result,model=self.run_response(first,repair)
        self.assertEqual(model.call_count,2)
        self.assertIs(model.call_args.kwargs['response_schema'],q.QuickRepairResponse)
        data=json.loads(model.call_args.kwargs['prompt'])
        self.assertEqual(data['correction']['requested_quality_ids'],['FR-2'])
        self.assertFalse(data['correction']['request_consistency'])
        self.assertEqual(result.quality.assessments[0].quality_score,70)
        self.assertEqual(result.consistency.findings[0].category,'contradiction')

    def test_duplicate_and_inconsistent_status_targeted(self):
        good=fixture(); first=copy.deepcopy(good)
        first['quality']['assessments'][0]['status']='passed'
        first['quality']['assessments'].append(copy.deepcopy(first['quality']['assessments'][1]))
        result,model=self.run_response(first,{'quality':good['quality'],'consistency':None})
        diag=json.loads(model.call_args.kwargs['prompt'])['correction']['quality_diagnostics']
        self.assertEqual({d['requirement_id']:d['code'] for d in diag},
                         {'FR-1':'status_findings_mismatch','FR-2':'duplicate_assessment'})
        self.assertEqual(len(result.quality.assessments),2)

    def test_consistency_only_repair(self):
        good=fixture(); first=copy.deepcopy(good)
        first['consistency']['findings'][0]['requirement_ids']=['FR-1']
        result,model=self.run_response(first,{'quality':{'assessments':[]},'consistency':good['consistency']})
        payload=json.loads(model.call_args.kwargs['prompt'])['correction']
        self.assertEqual(payload['requested_quality_ids'],[])
        self.assertTrue(payload['request_consistency'])
        self.assertEqual(len(result.consistency.findings[0].evidence),2)

    def test_wrong_legacy_evidence_not_silently_overwritten(self):
        first=fixture(); first['quality']['assessments'][0]['findings'][0].update(requirement_id='FR-1',evidence='fabricated')
        with patch.object(q,'ask_gemini',return_value=json.dumps(first)) as model:
            with self.assertRaisesRegex(q.QuickValidationError,'invalid_quality_ids_or_evidence'):
                q.quick_validate(SECTIONS)
        self.assertEqual(model.call_count,2)
        self.assertEqual(json.loads(model.call_args.kwargs['prompt'])['correction']['quality_diagnostics'][0]['code'],'invalid_quality_quotation')

    def test_bad_extraction_and_unknown_or_one_sided_consistency_remain_rejected(self):
        for kind in ('extraction','unknown','one_sided','duplicate'):
            bad=fixture()
            if kind=='extraction': bad['extraction']['requirements'][0]['text']='fabricated'
            else: bad['consistency']['findings'][0]['requirement_ids']={'unknown':['FR-1','FR-99'],'one_sided':['FR-1'],'duplicate':['FR-1','FR-1']}[kind]
            with self.subTest(kind=kind),patch.object(q,'ask_gemini',return_value=json.dumps(bad)) as model:
                with self.assertRaises(q.QuickValidationError): q.quick_validate(SECTIONS)
                self.assertEqual(model.call_count,2)

    def test_unresolved_missing_assessment_is_not_fabricated(self):
        first=fixture();first['quality']['assessments'].pop()
        with patch.object(q,'ask_gemini',side_effect=[json.dumps(first),json.dumps({'quality':{'assessments':[]}})]) as model:
            with self.assertRaisesRegex(q.QuickValidationError,'invalid_quality_ids_or_evidence'): q.quick_validate(SECTIONS)
        self.assertEqual(model.call_count,2)

    def test_provider_failure_bounded_no_false_report(self):
        first=fixture();first['quality']['assessments'].pop()
        with patch.object(q,'ask_gemini',side_effect=[json.dumps(first),GeminiRequestError('quota',429)]) as model:
            with self.assertRaises(GeminiRequestError): q.quick_validate(SECTIONS)
        self.assertEqual(model.call_count,2)

    def test_retry_cannot_replace_extraction_or_accepted_assessment(self):
        first=fixture();first['quality']['assessments'].pop()
        second=fixture();second['quality']['assessments'][0]['quality_score']=1
        result,_=self.run_response(first,second)
        self.assertEqual(result.quality.assessments[0].quality_score,70)
        second['extraction']['requirements'][0]['text']='fabricated'
        with patch.object(q,'ask_gemini',side_effect=[json.dumps(first),json.dumps(second)]):
            with self.assertRaises(q.QuickValidationError): q.quick_validate(SECTIONS)
