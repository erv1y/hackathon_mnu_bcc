from core.models import Requirement, ExtractionResult, ConsistencyAnalysisResult
from core.rule_engine import check_rules, augment_consistency


def req(id,text,paragraph=1,source='demo.md'):
    return Requirement(id=id,type='functional',text=text,source=source,paragraph=paragraph)


def run():
    method=req('A','| **REST type** | GET |')
    write=req('B','4. Заменить настройку и вернуть результат сохранения.',2)
    sections=[{'source':'demo.md','paragraph':1,'section':'API / REST API / Method'},
              {'source':'demo.md','paragraph':2,'section':'API / REST API / Logic'}]
    e=ExtractionResult(requirements=[method,write])
    f=check_rules(e,sections)
    assert len(f)==1 and f[0].requirement_ids==['A','B']
    assert [x.quote for x in f[0].evidence]==[method.text,write.text]
    print('[PASS] GET with explicit write and source evidence')
    assert not check_rules(e,[])
    assert not check_rules(e,[sections[0],dict(sections[1],section='Other / REST API / Logic')])
    assert not check_rules(ExtractionResult(requirements=[method,req('B','Read settings',2)]),sections)
    assert not check_rules(ExtractionResult(requirements=[method,write,req('C','| **REST type** | POST |',2)]),sections)
    print('[PASS] Read-only, missing scope, separate scope and multiple methods')
    a=req('C','Limit: 1–5 KZT'); b=req('D','Limit: 10–20 KZT')
    assert len(check_rules(ExtractionResult(requirements=[a,b])))==1
    assert not check_rules(ExtractionResult(requirements=[a,req('D','Limit: 5–20 KZT')]))
    assert not check_rules(ExtractionResult(requirements=[a,req('D','Other: 10–20 KZT')]))
    assert not check_rules(ExtractionResult(requirements=[a,req('D','Limit: 10–20 USD')]))
    print('[PASS] Disjoint intervals and negative cases')
    baseline=ConsistencyAnalysisResult(findings=f,summary='test')
    merged=augment_consistency(e,baseline,sections)
    assert len(merged.findings)==2 and len({x.id for x in merged.findings})==2
    assert baseline.findings[0].id=='RULE-001'
    print('[PASS] Unique IDs and non-destructive merge')
    print('[TEST PASSED] Rule Engine offline tests')


if __name__=='__main__':
    run()
