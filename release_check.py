"""Replay the saved BCC analysis locally. No model requests; leaves originals untouched."""
import argparse
import json
from collections import Counter
from pathlib import Path
from time import perf_counter
from core.document_reader import read_document
from core.models import ExtractionResult, QualityAnalysisResult, ConsistencyAnalysisResult, AuditResult, ValidationResult
from core.release_review import finalize_review


def load(p, schema): return schema.model_validate_json(p.read_text(encoding='utf-8-sig'))

def replay(document, reports, output):
    from agents.auditor import validate_audit, prepare_findings, audit_trace
    from agents.consistency_analyst import validate_findings
    started=perf_counter()
    extraction=load(reports/'extraction_v08.json', ExtractionResult)
    quality=load(reports/'quality_v08.json', QualityAnalysisResult)
    audit=load(reports/'audit_v08.json', AuditResult)
    trace=json.loads((reports/'audit_trace_v08.json').read_text(encoding='utf-8-sig'))
    if trace['audit']!=audit.model_dump(mode='json'): raise ValueError('Saved audit and audit trail do not match')
    if (reports/'direct_v08.json').is_file():
        consistency=load(reports/'direct_v08.json', ConsistencyAnalysisResult)
        baseline_source='direct_v08.json'
    else:
        consistency=ConsistencyAnalysisResult(findings=[f for f in trace['original_findings'] if f['agent']!='quality_analyst'], summary='Восстановлено из сохранённой истории аудита.')
        baseline_source='audit_trace_v08.json (original_findings)'
    originals=prepare_findings(quality,consistency)
    if originals!=trace['original_findings']: raise ValueError('Saved candidate inputs do not match audit trail')
    if Counter(a.requirement_id for a in quality.assessments)!=Counter(r.id for r in extraction.requirements): raise ValueError('Quality registry mismatch')
    validate_findings(consistency,extraction); validate_audit(audit,originals,extraction)
    sections=read_document(document)
    con, final, debug=finalize_review(extraction,consistency,audit,sections)
    validate_findings(con,extraction); validate_audit(final,prepare_findings(quality,con),extraction)
    result=ValidationResult(source_files=[document.name],execution_time_seconds=round(perf_counter()-started,3),extraction=extraction,quality=quality,consistency=con,audit=final)
    # This is a historical replay, never represented as a fresh AI run.
    data=result.model_dump(mode='json'); data['passport']={'mode':'offline_replay','run_kind':'saved_analysis_replay','baseline_source':baseline_source,'new_api_calls':0,'release_review':debug}
    output.mkdir(parents=True,exist_ok=True)
    for name,obj in [('final_validation.json',data),('audit_trace.json',audit_trace(final,prepare_findings(quality,con))),('release_review.json',dict(debug,pre_review_audit=trace['audit']))]:
        (output/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# Локальная перепроверка сохранённого анализа BCC','', 'Это повторная обработка исторических AI-результатов, не новый AI-анализ. Новых вызовов API: 0.','',final.executive_summary,'','## Программные кандидаты для ручной проверки','']
    for d in final.decisions:
        if not d.finding_id.startswith('REVIEW-'): continue
        lines += [f'### {d.finding_id} — {d.title} ({d.final_severity})','',d.explanation,'']
        for e in d.final_evidence: lines += [f'- {e.requirement_id} ({e.source}, блок {e.paragraph}): {e.quote}']
        lines += ['',d.recommendation,'']
    lines += ['## Перенесённые доказательства из объединённых замечаний','']
    for x in debug['restored_group_evidence']: lines += [f'- {x["member"]} → {x["representative"]}: {x["requirement_id"]}']
    lines += ['','## Ограничения','']+['- '+x for x in final.limitations]
    (output/'review.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(f'[REPLAY] 0 new API calls; {len(debug["added_review_ids"])} added review alerts; {len(debug["restored_group_evidence"])} restored evidence records')
    print(final.executive_summary)
    print('[SAVED]', output)
    return result,debug

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--document',type=Path,default=Path('docs/requirements.md')); p.add_argument('--reports',type=Path,default=Path('reports')); p.add_argument('--output',type=Path,default=Path('reports/stable_replay')); a=p.parse_args()
    if a.reports.resolve()==a.output.resolve(): p.error('Use a separate output folder')
    replay(a.document,a.reports,a.output)
