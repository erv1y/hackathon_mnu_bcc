"""Direct, evidence-grounded pair review; AI conclusions remain candidates."""
import json
import math
from collections import Counter
from pathlib import Path
from typing import Literal
from pydantic import BaseModel
from core.models import ConsistencyAnalysisResult, ConsistencyFinding, FindingEvidence
from core.business_logic import select_pairs, PAIR_BATCH

PROMPT_PATH = Path(__file__).resolve().parent.parent / 'prompts' / 'counterexample.txt'

class Side(BaseModel):
    requirement_id: str
    quote: str
    expected_result: str

class DirectReview(BaseModel):
    pair_id: str
    verdict: Literal['contradiction', 'priority_ambiguity', 'compatible', 'not_comparable', 'unresolved']
    scope: str
    scenario: str
    a: Side
    b: Side
    rationale: str
    severity: Literal["critical", "high", "medium", "low"] | None = None
    title: str
    recommendation: str

class DirectResult(BaseModel):
    reviews: list[DirectReview]

CANDIDATE = {'contradiction', 'priority_ambiguity'}

def norm(text):
    return ' '.join(text.split())


def cells(line):
    line = line.strip()
    if not line.startswith('|') or not line.endswith('|') or '\\|' in line:
        return []
    return [x.strip() for x in line[1:-1].split('|')]


def acceptance_expected(requirement, sections):
    """Lock only simple three-column Markdown cases with an explicit result header."""
    row = cells(requirement.text)
    if requirement.type != 'acceptance_criterion' or len(row) != 3 or not all(row):
        return None
    for s in sections or []:
        if (s['source'], s.get('page'), s.get('paragraph')) != (requirement.source, requirement.page, requirement.paragraph):
            continue
        header = None
        for line in s['text'].splitlines():
            cols = cells(line)
            if len(cols) != 3:
                header = None
                continue
            if cols[2].casefold().strip('*') in ('ожидаемый результат', 'expected result', 'expected outcome'):
                header = cols
            elif header and norm(line) == norm(requirement.text):
                return row[2]
    return None


def validate_reviews(result, batch, reqs, locked):
    expected = {p['pair_id']:p['requirement_ids'] for p in batch}
    if len(result.reviews) != len(expected) or {r.pair_id for r in result.reviews} != set(expected):
        raise ValueError('Return exactly one review per assigned pair')
    for r in result.reviews:
        if [r.a.requirement_id, r.b.requirement_id] != expected[r.pair_id]:
            raise ValueError(f'{r.pair_id}: wrong or reversed requirement IDs')
        if not norm(r.rationale):
            raise ValueError(f'{r.pair_id}: missing rationale')
        for side in (r.a, r.b):
            rid = side.requirement_id
            if not norm(side.quote) or norm(side.quote) not in norm(reqs[rid].text):
                raise ValueError(f'{r.pair_id}: invalid source quote for {rid}')
            if rid in locked:
                if norm(side.expected_result) != norm(locked[rid]):
                    raise ValueError(f'{r.pair_id}: acceptance expected_result must copy the locked cell for {rid}')
                if norm(locked[rid]) not in norm(side.quote):
                    raise ValueError(f'{r.pair_id}: acceptance quote must include the locked result for {rid}')
        if r.verdict in CANDIDATE or r.verdict == 'compatible':
            if not all(norm(x) for x in (r.scope, r.scenario, r.a.expected_result, r.b.expected_result)):
                raise ValueError(f'{r.pair_id}: missing shared scenario or outcomes')
        if r.verdict in CANDIDATE:
            if not norm(r.title) or not norm(r.recommendation):
                raise ValueError(f'{r.pair_id}: missing title or recommendation')
            if norm(r.a.expected_result).casefold() == norm(r.b.expected_result).casefold():
                raise ValueError(f'{r.pair_id}: identical expected results cannot establish a conflict')


def analyze_counterexamples(extraction, consistency, sections=None, debug=None):
    from core.llm_client import request_language, ask_gemini
    from agents.consistency_analyst import validate_findings
    validate_findings(consistency, extraction)
    reqs = {r.id:r for r in extraction.requirements}
    pairs = select_pairs(extraction, sections)
    locked = {r.id:v for r in reqs.values() if (v := acceptance_expected(r, sections)) is not None}
    instruction = PROMPT_PATH.read_text(encoding='utf-8')
    diagnostic = debug if debug is not None else {}
    diagnostic.clear()
    diagnostic.update(version='direct-1.0', selected_pairs=pairs, passes=[], complete=False,
                      total_possible_pairs=len(reqs)*(len(reqs)-1)//2,
                      locked_acceptance_ids=sorted(locked))
    findings = list(consistency.findings)
    used = {f.id for f in findings}
    reviewed, unresolved, covered, candidates = [], [], set(), []
    for offset in range(0,len(pairs),PAIR_BATCH):
        batch = pairs[offset:offset+PAIR_BATCH]
        entry = {'pass':f'batch_{offset//PAIR_BATCH+1}', 'attempts':[], 'status':'pending'}
        diagnostic['passes'].append(entry)
        pair_payload = {p['pair_id']: dict(p, requirements=[
            dict(reqs[i].model_dump(mode='json'), locked_expected_result=locked.get(i))
            for i in p['requirement_ids']]) for p in batch}
        pending = {p['pair_id']: p for p in batch}
        retained = {}
        payload = {'source_sections': sections or []}
        print(f'[DIRECT LOGIC] batch {offset//PAIR_BATCH+1}/{math.ceil(len(pairs)/PAIR_BATCH)}')
        for attempt in range(1, 3):
            payload['pairs'] = [pair_payload[pid] for pid in pending]
            try:
                raw = ask_gemini(prompt=json.dumps(payload, ensure_ascii=False), system_instruction=instruction,
                                 model='gemini-3.5-flash-lite', response_schema=DirectResult)
            except Exception as exc:
                entry['attempts'].append({'attempt': attempt, 'error_type': type(exc).__name__})
                entry['status'] = 'api_failed'
                print(f'[DIRECT REVIEW] API failed: {type(exc).__name__}')
                break
            try:
                raw = raw.strip()
                if raw.startswith('```'):
                    raw = raw.removeprefix('```json').removeprefix('```').removesuffix('```').strip()
                rows = json.loads(raw)['reviews']
                if not isinstance(rows, list):
                    raise ValueError('invalid envelope')
            except (ValueError, TypeError, KeyError, RecursionError):
                rows = []
            counts = Counter(row['pair_id'] for row in rows if isinstance(row, dict)
                             and isinstance(row.get('pair_id'), str))
            errors = {}
            for row in rows:
                pid = row.get('pair_id') if isinstance(row, dict) else None
                if not isinstance(pid, str) or pid not in pending or counts[pid] != 1:
                    continue
                try:
                    review = DirectReview.model_validate(row)
                except ValueError:
                    errors[pid] = 'invalid review schema'
                    continue
                try:
                    validate_reviews(DirectResult(reviews=[review]), [pending[pid]], reqs, locked)
                except ValueError as error:
                    # validate_reviews emits only fixed rule text and verified identifiers.
                    errors[pid] = str(error)
                    continue
                retained[pid] = review
                del pending[pid]
            if not pending:
                entry['attempts'].append({'attempt': attempt, 'status': 'valid'})
                break
            for pid in pending:
                errors.setdefault(pid, 'duplicate review' if counts[pid] > 1 else 'missing review')
            entry['attempts'].append({'attempt': attempt, 'error': '; '.join(errors.values()), 'rules': errors})
            payload['validation_error'] = errors
            print(f'[DIRECT CORRECTION] {len(pending)} missing or invalid pair reviews')
        if entry['status'] != 'api_failed':
            entry['status'] = 'validation_failed' if pending else 'checked'
        unresolved.extend(pending)
        result = DirectResult(reviews=[retained[p['pair_id']] for p in batch if p['pair_id'] in retained])
        entry['reviews'] = result.model_dump(mode='json')['reviews']
        for r in result.reviews:
            if r.verdict == 'unresolved':
                unresolved.append(r.pair_id)
                continue
            reviewed.append(r.pair_id)
            ids = [r.a.requirement_id,r.b.requirement_id]
            covered.update(ids)
            if r.verdict not in CANDIDATE: continue
            evidence = [FindingEvidence(requirement_id=s.requirement_id,quote=s.quote,
                            source=reqs[s.requirement_id].source,page=reqs[s.requirement_id].page,
                            paragraph=reqs[s.requirement_id].paragraph) for s in (r.a,r.b)]
            n=1
            while f'LOGIC-{n:03d}' in used: n+=1
            fid=f'LOGIC-{n:03d}'
            used.add(fid)
            scope_label, scenario_label, reason_label, impact = {
                "ru": ("Область", "Гипотетический сценарий", "Обоснование",
                       "При подтверждении интерпретации возможны разные результаты в одном сценарии."),
                "en": ("Scope", "Hypothetical scenario", "Rationale",
                       "If this interpretation is confirmed, the same scenario can have different outcomes."),
                "kk": ("Қолдану аясы", "Болжамды сценарий", "Негіздеме",
                       "Бұл түсіндірме расталса, бір сценарийдің нәтижелері әртүрлі болуы мүмкін."),
            }[request_language.get()]
            f = ConsistencyFinding(
                id=fid, category='contradiction' if r.verdict == 'contradiction' else 'inconsistent_constraint',
                severity=r.severity or ('high' if r.verdict == 'contradiction' else 'medium'),
                severity_rationale=r.rationale, requirement_ids=ids, title=r.title, evidence=evidence,
                explanation=f'[DIRECT_COUNTEREXAMPLE] {r.pair_id}. {scope_label}: {r.scope}. '
                            f'{scenario_label}: {r.scenario}. {ids[0]}: {r.a.expected_result}. '
                            f'{ids[1]}: {r.b.expected_result}. {reason_label}: {r.rationale}.',
                impact=impact, recommendation=r.recommendation, confidence=0.65)
            findings.append(f)
            candidates.append(fid)
    diagnostic.update(reviewed_pair_ids=sorted(reviewed),unresolved_pair_ids=sorted(unresolved),
                      covered_ids=sorted(covered),uncovered_ids=sorted(set(reqs)-covered),
                      candidate_count=len(candidates),candidate_ids=candidates,
                      complete=bool(pairs) and not unresolved and len(reviewed)==len(pairs))
    print(f'[PAIR COVERAGE] {len(reviewed)}/{len(pairs)} selected pairs reviewed by AI; {len(unresolved)} unresolved; {len(candidates)} candidates')
    result = ConsistencyAnalysisResult(repair_diagnostics=consistency.repair_diagnostics, unresolved=consistency.unresolved,findings=findings,summary=consistency.summary+
        f' | Прямой анализ: {len(reviewed)}/{len(pairs)} выбранных пар получили заключение AI; '
        f'{len(unresolved)} не разрешены; {len(candidates)} кандидатов. Всего возможных пар: {diagnostic["total_possible_pairs"]}. '
        'Не является доказательством полноты или отсутствия дефектов.')
    validate_findings(result, extraction)
    return result


def main():
    import argparse
    from core.document_reader import read_document
    from core.models import ExtractionResult
    p=argparse.ArgumentParser(description='Direct evidence-grounded counterexample analysis')
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--document',type=Path,nargs='+',required=True)
    p.add_argument('--consistency',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--debug',type=Path)
    a=p.parse_args()
    extraction=ExtractionResult.model_validate_json(a.input.read_text(encoding='utf-8-sig'))
    sections=[s for doc in a.document for s in read_document(doc)]
    for r in extraction.requirements:
        if not any((s['source'],s.get('page'),s.get('paragraph'))==(r.source,r.page,r.paragraph) and norm(r.text) in norm(s['text']) for s in sections):
            raise ValueError(f'Document does not match extraction: {r.id}')
    baseline=ConsistencyAnalysisResult.model_validate_json(a.consistency.read_text(encoding='utf-8-sig')) if a.consistency else ConsistencyAnalysisResult(findings=[],summary='Прямой анализ.')
    debug={}
    result=analyze_counterexamples(extraction,baseline,sections,debug)
    debug_path=a.debug or a.output.with_name('counterexample_debug.json')
    if debug_path.resolve()==a.output.resolve(): raise ValueError('Debug and result paths must differ')
    debug_path.parent.mkdir(parents=True,exist_ok=True)
    debug_path.write_text(json.dumps(debug,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'[SAVED] {debug_path}')
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(result.model_dump_json(indent=2),encoding='utf-8')
    for f in result.findings:
        print(f'{f.id} [{f.severity}] {f.title}: {", ".join(f.requirement_ids)}')
        if f.id.startswith('LOGIC-'): print(f.explanation)
    print(f'[SAVED] {a.output}')

if __name__=='__main__': main()
