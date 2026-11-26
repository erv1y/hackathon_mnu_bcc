"""Bounded symbolic scenario checking. Model formalizations are hypotheses."""
import json
import math
import operator
from itertools import combinations, product
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from core.models import ConsistencyAnalysisResult, ConsistencyFinding, FindingEvidence

BASE_DIR = Path(__file__).resolve().parent.parent
PROMPT_PATH = BASE_DIR / 'prompts' / 'business_logic.txt'
MAX_ASSIGNMENTS = 1024
MAX_SCENARIOS = 16
MAX_RULES = 12
Kind = Literal['integer', 'boolean', 'symbol']

class Variable(BaseModel):
    name: str
    kind: Kind
    domain: list[str]

class Operand(BaseModel):
    kind: Literal['variable', 'literal']
    value: str

class Condition(BaseModel):
    left: str
    op: Literal['eq', 'ne', 'lt', 'le', 'gt', 'ge']
    right: Operand

class Rule(BaseModel):
    requirement_id: str
    conditions: list[Condition]
    output: Operand
    interpretation: str

class Scenario(BaseModel):
    title: str
    scope: str
    output_name: str
    output_kind: Kind
    variables: list[Variable]
    assumptions: list[Condition]
    rules: list[Rule]

class FormalizationResult(BaseModel):
    scenarios: list[Scenario]

OPS = {'eq': operator.eq, 'ne': operator.ne, 'lt': operator.lt,
       'le': operator.le, 'gt': operator.gt, 'ge': operator.ge}

def parse_value(value, kind):
    if not value or len(value) > 160:
        raise ValueError('Empty or oversized operand')
    if kind == 'boolean':
        if value not in ('true', 'false'):
            raise ValueError('Boolean values must be true or false')
        return value == 'true'
    if kind == 'integer':
        if len(value) > 24 or not value.lstrip('-').isascii() or not value.lstrip('-').isdigit():
            raise ValueError('Integer values must be plain decimal strings')
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError('Invalid integer') from exc
    return value


def check_scenarios(formalization, extraction):
    """Validate schema semantics, enumerate finite domains, emit one witness per pair/target.

    A witness proves a conflict only in the proposed model, not in natural language.
    """
    reqs = {r.id: r for r in extraction.requirements}
    if len(reqs) != len(extraction.requirements):
        raise ValueError('Duplicate extraction IDs')
    if len(formalization.scenarios) > MAX_SCENARIOS:
        raise ValueError('Too many scenarios')
    findings, seen = [], set()
    checked = 0
    for s in formalization.scenarios:
        if not all(x.strip() for x in (s.title,s.scope,s.output_name)):
            raise ValueError('Missing scenario scope or output')
        if len(s.variables) > 10 or not 2 <= len(s.rules) <= MAX_RULES:
            raise ValueError('Scenario exceeds variable/rule limits or lacks comparison')
        variables = {v.name:v for v in s.variables}
        if len(variables) != len(s.variables) or any(not k.strip() for k in variables):
            raise ValueError('Duplicate or empty variable name')
        domains = []
        for v in s.variables:
            if not 1 <= len(v.domain) <= 8:
                raise ValueError('Domain size must be 1..8')
            domains.append(list(dict.fromkeys(parse_value(x,v.kind) for x in v.domain)))
        if math.prod(map(len,domains)) > MAX_ASSIGNMENTS:
            raise ValueError('Scenario domain exceeds 1024 assignments')

        def validate_operand(operand, kind):
            if operand.kind == 'variable':
                if operand.value not in variables or variables[operand.value].kind != kind:
                    raise ValueError('Unknown variable or mismatched operand type')
            else:
                parse_value(operand.value,kind)

        def validate_condition(c):
            if c.left not in variables:
                raise ValueError('Unknown condition variable')
            kind = variables[c.left].kind
            if c.op not in ('eq','ne') and kind != 'integer':
                raise ValueError('Ordering only allowed for integers')
            validate_operand(c.right,kind)

        def operand_value(o, kind, env):
            return env[o.value] if o.kind == 'variable' else parse_value(o.value,kind)

        def applies(conditions, env):
            return all(OPS[c.op](env[c.left],operand_value(c.right,variables[c.left].kind,env)) for c in conditions)

        if len(s.assumptions) > 20:
            raise ValueError('Too many assumptions')
        for c in s.assumptions:
            validate_condition(c)
        for r in s.rules:
            if r.requirement_id not in reqs or not r.interpretation.strip():
                raise ValueError('Unknown requirement or missing interpretation')
            if len(r.conditions) > 20:
                raise ValueError('Too many conditions')
            for c in r.conditions:
                validate_condition(c)
            validate_operand(r.output,s.output_kind)
        if len({r.requirement_id for r in s.rules}) < 2:
            raise ValueError('Scenario requires distinct source IDs')

        # Complete enumeration within this bounded model, including assumptions.
        envs = [dict(zip(variables, values)) for values in product(*domains)]
        feasible = 0
        for env in envs:
            if not applies(s.assumptions,env):
                continue
            checked += 1
            feasible += 1
            applicable = [r for r in s.rules if applies(r.conditions,env)]
            for a,b in combinations(applicable,2):
                if a.requirement_id == b.requirement_id:
                    continue
                va = operand_value(a.output,s.output_kind,env)
                vb = operand_value(b.output,s.output_kind,env)
                if va == vb:
                    continue
                ids = [a.requirement_id,b.requirement_id]
                key = (tuple(sorted(ids)),s.scope.strip().casefold(),s.output_name.strip().casefold())
                if key in seen:
                    continue
                seen.add(key)
                # JSON shows hypothetical values without Python bool spelling.
                witness = json.dumps(env,ensure_ascii=False,sort_keys=True)
                def evidence(rid):
                    r=reqs[rid]
                    return FindingEvidence(requirement_id=rid,quote=r.text,source=r.source,page=r.page,paragraph=r.paragraph)
                findings.append(ConsistencyFinding(
                    id=f'LOGIC-{len(findings)+1:03d}',category='contradiction',severity='high',
                    requirement_ids=ids,title=f'Потенциальный конфликт сценария: {s.title}',
                    evidence=[evidence(i) for i in ids],
                    explanation=(f'[SYMBOLIC_WITNESS] Область: {s.scope}. Гипотетический вход: {witness}. '
                        f'Интерпретация {ids[0]}: {a.interpretation}. Интерпретация {ids[1]}: {b.interpretation}. '
                        f'Оба условия истинны, но {s.output_name} принимает несовместимые значения: '
                        f'{json.dumps(va,ensure_ascii=False)} и {json.dumps(vb,ensure_ascii=False)}. '
                        'Проверка выполнена программно для AI-формализации; необходимо подтвердить её по исходным цитатам, включая исключения.'),
                    impact='Если интерпретации верны, один сценарий имеет два несовместимых ожидаемых результата.',
                    recommendation='Согласовать условие применения, приоритет или исключение и зафиксировать единый ожидаемый результат в спецификации и тестах.',
                    confidence=0.65))
        if not feasible:
            print(f'[LOGIC REVIEW] No feasible assignments: {s.title}')
    print(f'[BUSINESS LOGIC] {len(formalization.scenarios)} scenarios, {checked} feasible assignments, {len(findings)} candidates')
    return findings


PAIR_BUDGET = 24
PAIR_BATCH = 4

class PairReview(BaseModel):
    pair_id: str
    status: Literal['modeled', 'not_comparable', 'unrepresentable']
    reason: str
    scenarios: list[Scenario]

class PairResult(BaseModel):
    reviews: list[PairReview]

class SemanticReview(BaseModel):
    pair_id: str
    decision: Literal['accept', 'revise', 'uncertain']
    reason: str
    scenarios: list[Scenario]

class SemanticResult(BaseModel):
    reviews: list[SemanticReview]


def select_pairs(extraction, sections=None):
    """Rank shared lexical stems and behavior markers; no case-specific IDs."""
    import re
    from collections import Counter
    stop = set('при для или что это как его она они один the and for with from that must shall будет после'.split())
    def terms(r):
        return {w[:5] for w in re.findall(r'[^\W\d_]+',r.text.casefold()) if len(w)>=4 and w not in stop}
    groups = [r'месяц|месяц|месяч|январ|феврал|дат[аы]|врем[яе]|period|month|date|timestamp|cutoff',
              r'измен|замен|сохран|повтор|предыдущ|истори|change|reset|previous|repeat|update',
              r'максим|миним|порог|лимит|threshold|limit|quota|maximum|minimum',
              r'отправ|уведом|push|notify|notification|send',
              r'покуп|операц|transaction|purchase|posting|payment',
              r'возврат|refund|retry|идемпотент|duplicate']
    reqs = extraction.requirements
    tokens = {r.id:terms(r) for r in reqs}
    freq = Counter(t for v in tokens.values() for t in v)
    markers = {r.id:{i for i,p in enumerate(groups) if re.search(p,r.text,re.I)} for r in reqs}
    ctx = {(s['source'],s.get('page'),s.get('paragraph')):s.get('section','') for s in sections or []}
    def api(r):
        return bool(re.search(r'REST API|HTTP API',ctx.get((r.source,r.page,r.paragraph),''),re.I))
    ranked=[]
    for a,b in combinations(reqs,2):
        shared=tokens[a.id]&tokens[b.id]; common=markers[a.id]&markers[b.id]
        if not shared and not common: continue
        score=sum(1/math.sqrt(freq[t]) for t in shared)+1.3*len(common)
        if a.type=='acceptance_criterion' or b.type=='acceptance_criterion': score+=1.5
        if a.type==b.type=='acceptance_criterion': score-=1.5
        if api(a) and api(b): score-=3
        if api(a)!=api(b): score-=1
        if a.paragraph is not None and (a.source,a.page,a.paragraph)==(b.source,b.page,b.paragraph): score+=0.7
        ranked.append((score,a.id,b.id))
    ranked.sort(key=lambda x:(-x[0],x[1],x[2]))
    return [{'pair_id':f'PAIR-{i:03d}','requirement_ids':[a,b],'score':round(score,3)} for i,(score,a,b) in enumerate(ranked[:PAIR_BUDGET],1)]


def pair_models(reviews, pairs, extraction, semantic=False):
    expected={p['pair_id']:set(p['requirement_ids']) for p in pairs}
    if len(reviews)!=len(expected) or {r.pair_id for r in reviews}!=set(expected):
        raise ValueError('Return exactly one review per requested pair')
    for review in reviews:
        if not review.reason.strip(): raise ValueError('Missing review reason')
        if len(review.scenarios)>3: raise ValueError('Maximum three scenarios per pair')
        if not semantic and (review.status=='modeled')!=bool(review.scenarios):
            raise ValueError('Modeled status requires scenarios; other statuses require none')
        if semantic and review.decision=='uncertain' and review.scenarios:
            raise ValueError('Uncertain review must not submit scenarios')
        for s in review.scenarios:
            if {r.requirement_id for r in s.rules}!=expected[review.pair_id]:
                raise ValueError('Scenario must compare exactly its assigned pair')
        check_scenarios(FormalizationResult(scenarios=review.scenarios),extraction)


def formalize_and_check(extraction, consistency, sections=None, debug=None):
    from core.llm_client import ask_gemini
    prompt=PROMPT_PATH.read_text(encoding='utf-8')
    pairs=select_pairs(extraction,sections)
    reqs={r.id:r for r in extraction.requirements}
    diagnostic=debug if debug is not None else {}
    diagnostic.update(version='3.0',passes=[],selected_pairs=pairs,complete=False)
    candidates=[]; covered=set(); verified_pairs=[]; unresolved=[]; total_scenarios=0; seen=set()
    def request(payload, instruction, schema, validate, entry):
        for attempt in range(1,3):
            try:
                raw=ask_gemini(prompt=json.dumps(payload,ensure_ascii=False),system_instruction=instruction,model='gemini-3.5-flash-lite',response_schema=schema).strip()
                if raw.startswith('```'): raw=raw.removeprefix('```json').removeprefix('```').removesuffix('```').strip()
                result=schema.model_validate_json(raw)
                validate(result)
                return result
            except ValueError as exc:
                payload=dict(payload,validation_error=str(exc)); entry.append({'attempt':attempt,'error':str(exc)})
            except Exception as exc:
                entry.append({'attempt':attempt,'error_type':type(exc).__name__}); break
        return None
    for offset in range(0,len(pairs),PAIR_BATCH):
        batch=pairs[offset:offset+PAIR_BATCH]
        entry={'pass':f'batch_{offset//PAIR_BATCH+1}','attempts':[],'status':'pending'}
        diagnostic['passes'].append(entry)
        payload={'pairs':[dict(p,requirements=[reqs[i].model_dump(mode='json') for i in p['requirement_ids']]) for p in batch], 'source_sections':sections or []}
        print(f'[PAIR LOGIC] batch {offset//PAIR_BATCH+1}/{math.ceil(len(pairs)/PAIR_BATCH)}')
        first=request(payload,prompt+'\nFORMALIZATION: return PairResult with one review per pair. Model at most three scenarios per pair.',PairResult,lambda x:pair_models(x.reviews,batch,extraction),entry['attempts'])
        if first is None:
            entry['status']='formalization_failed'; unresolved.extend(p['pair_id'] for p in batch); continue
        entry['formalization']=first.model_dump(mode='json')
        second=request(dict(payload,proposed=entry['formalization']),prompt+'\nINDEPENDENT SEMANTIC REVIEW: return SemanticResult. Review even pairs marked not_comparable. Accept only faithful scenarios; revise by returning corrected scenarios. Return uncertain with no scenarios if unable to verify. Accept with no scenarios only if pair genuinely not comparable or unrepresentable, explain why. Check distinct event identities, all source conditions, exceptions, and whether a common input is possible. Do not restrict a general rule to a sample case without justification.',SemanticResult,lambda x:pair_models(x.reviews,batch,extraction,True),entry['attempts'])
        if second is None:
            entry['status']='semantic_failed'; unresolved.extend(p['pair_id'] for p in batch); continue
        entry['semantic_review']=second.model_dump(mode='json'); entry['status']='checked'
        for review in second.reviews:
            if review.decision=='uncertain' or not review.scenarios:
                unresolved.append(review.pair_id); continue
            verified_pairs.append(review.pair_id)
            for scenario in review.scenarios:
                total_scenarios+=1; covered.update(r.requirement_id for r in scenario.rules)
                for f in check_scenarios(FormalizationResult(scenarios=[scenario]),extraction):
                    key=(tuple(sorted(f.requirement_ids)),f.explanation)
                    if key not in seen: seen.add(key); candidates.append(f)
    used={f.id for f in consistency.findings}
    for f in candidates:
        n=1
        while f'LOGIC-{n:03d}' in used: n+=1
        f.id=f'LOGIC-{n:03d}'; used.add(f.id)
    all_ids=set(reqs)
    diagnostic.update(covered_ids=sorted(covered),uncovered_ids=sorted(all_ids-covered),scenario_count=total_scenarios,candidate_count=len(candidates),verified_pair_ids=verified_pairs,unresolved_pair_ids=unresolved,total_possible_pairs=len(reqs)*(len(reqs)-1)//2,complete=not unresolved and all(e['status']=='checked' for e in diagnostic['passes']))
    print(f'[PAIR COVERAGE] {len(verified_pairs)}/{len(pairs)} selected pairs modeled and AI-reviewed; {len(unresolved)} unresolved; {len(candidates)} candidates')
    return ConsistencyAnalysisResult(findings=[*consistency.findings,*candidates],summary=consistency.summary+f' | Парная проверка: {len(verified_pairs)}/{len(pairs)} выбранных пар формализованы и перепроверены AI, {len(unresolved)} не разрешены, {len(candidates)} кандидатов. Всего возможных пар: {diagnostic["total_possible_pairs"]}. Полнота и смысловая корректность не гарантированы.')


def main():
    import argparse
    from core.document_reader import read_document
    from core.models import ExtractionResult
    from agents.consistency_analyst import validate_findings
    p=argparse.ArgumentParser(description='Bounded business logic scenario analysis')
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--document',type=Path,nargs='+',required=True)
    p.add_argument('--consistency',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--debug',type=Path,help='Local diagnostic JSON; may contain source-derived data')
    a=p.parse_args()
    extraction=ExtractionResult.model_validate_json(a.input.read_text(encoding='utf-8'))
    sections=[s for doc in a.document for s in read_document(doc)]
    for r in extraction.requirements:
        if not any(s['source']==r.source and s.get('page')==r.page and s.get('paragraph')==r.paragraph and ' '.join(r.text.split()) in ' '.join(s['text'].split()) for s in sections):
            raise ValueError(f'Document does not match extraction: {r.id}')
    baseline=ConsistencyAnalysisResult.model_validate_json(a.consistency.read_text(encoding='utf-8')) if a.consistency else ConsistencyAnalysisResult(findings=[],summary='Символическая проверка.')
    validate_findings(baseline,extraction)
    debug = {}
    result=formalize_and_check(extraction,baseline,sections,debug)
    debug_path = a.debug or a.output.with_name('formalization_debug.json')
    debug_path.parent.mkdir(parents=True, exist_ok=True)
    debug_path.write_text(json.dumps(debug,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'[SAVED] {debug_path}')
    validate_findings(result,extraction)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(result.model_dump_json(indent=2),encoding='utf-8')
    for f in result.findings:
        print(f'{f.id} [{f.severity}] {f.title}: {", ".join(f.requirement_ids)}')
        if f.id.startswith('LOGIC-'):
            print(f.explanation)
    print(f'[SAVED] {a.output}')

if __name__ == '__main__':
    main()
