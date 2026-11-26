"""Offline tests: no Gemini requests; synthetic audit responses are fixtures."""
from copy import deepcopy
from unittest.mock import patch
from agents import auditor as a
from core.models import (Requirement, ExtractionResult, QualityFinding, RequirementAssessment,
                         QualityAnalysisResult, ConsistencyFinding, ConsistencyAnalysisResult,
                         FindingEvidence, AuditDecision, AuditResult, ExtractionAudit)


def fail(fn):
    try:
        fn()
    except ValueError:
        return
    raise AssertionError('Expected validation failure')


def fixtures():
    texts = ['A shipment may only be read by the public lookup endpoint.',
             'The public lookup endpoint changes the shipment status.',
             'The lookup endpoint has no documented response time.',
             'Invoices must be stored for twelve months.']
    reqs = [Requirement(id=f'R{i}', type='functional', text=text, source='sample.md', paragraph=i)
            for i, text in enumerate(texts, 1)]
    ex = ExtractionResult(requirements=reqs)
    sections = [dict(source=r.source, page=r.page, paragraph=r.paragraph, text=r.text) for r in reqs]
    def evidence(i):
        r = reqs[i-1]
        return FindingEvidence(requirement_id=r.id, quote=r.text, source=r.source, page=r.page, paragraph=r.paragraph)
    quality = QualityAnalysisResult(assessments=[RequirementAssessment(requirement_id=r.id, status='needs_revision' if i==1 else 'passed', quality_score=80 if i==1 else 100,
        findings=[QualityFinding(requirement_id=r.id, criterion='consistency', severity='high', evidence=r.text, explanation='Public endpoint conflicts with write.', recommendation='Separate read and write.', confidence=.8)] if i==1 else []) for i,r in enumerate(reqs,1)])
    consistency = ConsistencyAnalysisResult(summary='Fixture', findings=[ConsistencyFinding(id=f'CON-{i:03d}',category='contradiction' if i==1 else 'missing_requirement', severity='high' if i==1 else 'medium', requirement_ids=ids, title='Finding', evidence=[evidence(int(rid[1:])) for rid in ids], explanation='Initial candidate', impact='Potential issue', recommendation='Clarify', confidence=.8) for i,ids in enumerate([['R1','R2'],['R1','R3']],1)])
    def decision(fid, status, ids, cause, scope, severity=None, target=None):
        return AuditDecision(finding_id=fid, decision=status, final_severity=severity, requirement_ids=ids, title='Issue', explanation='Explanation', recommendation='Correct contract', audit_reason='Source reviewed', merged_into=target, confidence=.7, root_cause=cause, scope=scope, final_evidence=[evidence(int(rid[1:])) for rid in ids] if status!='rejected' else [])
    result = AuditResult(extraction_audit=ExtractionAudit(status='complete', missing_requirements=[], unsupported_requirements=[], comments='Fixture'), decisions=[decision('QUAL-001','merged',['R1'],'Read-write conflict','Lookup',target='CON-001'),decision('CON-001','confirmed',['R1','R2'],'Read-write conflict','Lookup','high'),decision('CON-002','revised',['R1','R3'],'Missing latency target','Lookup','medium')],readiness='ready', executive_summary='Fixture',priority_actions=['Correct contract'],limitations=[])
    return ex, sections, quality, consistency, result


def main():
    ex, sections, quality, consistency, result = fixtures()
    findings = a.prepare_findings(quality, consistency)
    original = deepcopy(findings)
    a.validate_audit(result, findings, ex)
    trace = a.audit_trace(result, findings)
    assert trace['input_count']==3 and trace['retained_count']==2 and trace['merged_count']==1
    assert trace['groups'][0]['members']==['CON-001','QUAL-001']
    assert findings==original
    print('[PASS] Cross-agent merge, original IDs, final evidence, raw audit trail')
    wrong = result.model_copy(deep=True); wrong.decisions[0].merged_into='CON-002'
    fail(lambda:a.validate_audit(wrong, findings, ex))
    print('[PASS] Different root causes stay separate even with a shared requirement')
    wrong = result.model_copy(deep=True); wrong.decisions[0].merged_into='QUAL-001'
    fail(lambda:a.validate_audit(wrong, findings, ex))
    wrong = result.model_copy(deep=True); wrong.decisions[1].decision='merged'; wrong.decisions[1].final_severity=None; wrong.decisions[1].merged_into='CON-002'
    fail(lambda:a.validate_audit(wrong, findings, ex))
    print('[PASS] Self-links and merge chains rejected')
    wrong = result.model_copy(deep=True); wrong.decisions[0].requirement_ids=['R4']; wrong.decisions[0].final_evidence=[FindingEvidence(requirement_id='R4',quote=ex.requirements[3].text,source='sample.md',paragraph=4)]
    fail(lambda:a.validate_audit(wrong, findings, ex))
    print('[PASS] Unrelated merge evidence rejected')
    wrong = result.model_copy(deep=True); wrong.decisions[1].final_evidence[0].quote='Fabricated text'
    fail(lambda:a.validate_audit(wrong, findings, ex))
    wrong = result.model_copy(deep=True); wrong.decisions[1].final_evidence[0].paragraph=8
    fail(lambda:a.validate_audit(wrong, findings, ex))
    wrong = result.model_copy(deep=True); wrong.decisions[1].final_evidence=[]
    fail(lambda:a.validate_audit(wrong, findings, ex))
    print('[PASS] Fabricated quotes, wrong source anchors and missing evidence rejected')
    wrong = result.model_copy(deep=True); wrong.decisions.pop()
    fail(lambda:a.validate_audit(wrong, findings, ex))
    wrong = result.model_copy(deep=True); wrong.decisions[1].audit_reason='null'
    fail(lambda:a.validate_audit(wrong, findings, ex))
    wrong = result.model_copy(deep=True); wrong.decisions[1].requirement_ids=['R1','R1','R2']
    fail(lambda:a.validate_audit(wrong, findings, ex))
    print('[PASS] Missing decisions, placeholders and duplicate IDs rejected')
    wrong = result.model_copy(deep=True); wrong.decisions[1].final_severity='medium'
    a.validate_audit(wrong, findings, ex); assert wrong.decisions[1].decision=='revised'
    assert a.apply_readiness_floor(result.model_copy(deep=True)).readiness=='not_ready'
    empty=result.model_copy(deep=True); empty.decisions=[]
    assert a.apply_readiness_floor(empty, {'complete':False}).readiness=='conditionally_ready'
    print('[PASS] Severity change normalization and conservative readiness floor')
    wrong = result.model_copy(deep=True); wrong.decisions[1].final_evidence[0].quote='Fake'
    with patch.object(a,'ask_gemini',side_effect=[wrong.model_dump_json(),result.model_dump_json()]) as mock:
        got=a.audit_results(sections,ex,quality,consistency,{'complete':True})
        assert mock.call_count==2 and got.readiness=='not_ready'
    print('[PASS] Mock API validation retry and merged output')
    invalid_sections=deepcopy(sections); invalid_sections[0]['text']='Different document'
    with patch.object(a,'ask_gemini') as mock:
        fail(lambda:a.audit_results(invalid_sections,ex,quality,consistency)); mock.assert_not_called()
    print('[PASS] Source mismatch fails before API call')
    old=result.model_dump(mode='json')
    for d in old['decisions']:
        for key in ('final_evidence','root_cause','scope'): d.pop(key)
    assert AuditResult.model_validate(old).decisions[0].final_evidence==[]
    print('[PASS] Legacy JSON loads (new audits require final evidence)')
    print('[TEST PASSED] Auditor 2.0 offline tests')


if __name__=='__main__': main()
