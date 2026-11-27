"""Two-pass consistency review; source evidence is materialized by Python."""
import argparse
import json
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field
from core.llm_client import ask_gemini
from core.models import (ExtractionResult, ConsistencyAnalysisResult,
                         ConsistencyFinding, FindingEvidence)

BASE_DIR = Path(__file__).resolve().parent.parent
PROMPT_PATH = BASE_DIR / 'prompts' / 'consistency_analyst.txt'
MAX_ATTEMPTS = 2


def clean_response(response):
    response = response.strip()
    if response.startswith('```'):
        response = response.removeprefix('```json').removeprefix('```').removesuffix('```')
    return response.strip()


class ConsistencyCandidate(BaseModel):
    category: Literal['contradiction','duplicate','missing_requirement','inconsistent_constraint',
                      'missing_dependency','scope_conflict','terminology_inconsistency']
    severity: Literal['critical','high','medium','low']
    requirement_ids: list[str] = Field(min_length=1)
    title: str
    explanation: str
    impact: str
    recommendation: str
    confidence: float = Field(ge=0, le=1)


class ConsistencyCandidateResult(BaseModel):
    findings: list[ConsistencyCandidate]
    summary: str


def validate_findings(result, extraction_result):

    requirements = {
        r.id: r
        for r in extraction_result.requirements
    }

    if len(requirements) != len(
        extraction_result.requirements
    ):
        raise ValueError(
            "Duplicate requirement IDs detected."
        )

    finding_ids = set()

    for finding in result.findings:

        if finding.id in finding_ids:
            raise ValueError(
                f"Duplicate finding ID: {finding.id}"
            )

        finding_ids.add(finding.id)

        for requirement_id in finding.requirement_ids:

            if requirement_id not in requirements:
                raise ValueError(
                    f"Unknown requirement ID: "
                    f"{requirement_id}"
                )

        if finding.category in (
            "contradiction",
            "duplicate",
            "inconsistent_constraint"
        ):

            if len(set(finding.requirement_ids)) < 2:
                raise ValueError(
                    f"{finding.id}: category "
                    f"{finding.category} requires "
                    "at least two distinct requirements."
                )

        evidence_ids = set()

        for evidence in finding.evidence:

            if (
                evidence.requirement_id
                not in finding.requirement_ids
            ):
                raise ValueError(
                    f"{finding.id}: evidence ID "
                    "does not match finding."
                )

            requirement = requirements[
                evidence.requirement_id
            ]

            evidence_ids.add(
                evidence.requirement_id
            )

            quote = " ".join(
                evidence.quote.split()
            )

            original_text = " ".join(
                requirement.text.split()
            )

            if not quote or quote not in original_text:
                raise ValueError(
                    f"Invalid quotation: {finding.id}"
                )

            if (
                evidence.source != requirement.source
                or evidence.page != requirement.page
                or evidence.paragraph != requirement.paragraph
            ):
                raise ValueError(
                    f"Incorrect source: {finding.id}"
                )

        if evidence_ids != set(
            finding.requirement_ids
        ):
            raise ValueError(
                f"Missing evidence: {finding.id}"
            )

    return result


def materialize(candidate, extraction):
    requirements = {r.id: r for r in extraction.requirements}
    findings = []
    for index, f in enumerate(candidate.findings, 1):
        ids = f.requirement_ids
        if not ids or len(ids) != len(set(ids)) or any(i not in requirements for i in ids):
            raise ValueError(f'Finding {index}: invalid or repeated requirement IDs')
        if f.category in ('contradiction', 'duplicate', 'inconsistent_constraint') and len(ids) < 2:
            raise ValueError(f'Finding {index}: two distinct requirements needed')
        evidence = [FindingEvidence(requirement_id=i, quote=requirements[i].text,
                   source=requirements[i].source, page=requirements[i].page,
                   paragraph=requirements[i].paragraph) for i in ids]
        findings.append(ConsistencyFinding.model_validate({**f.model_dump(),
            'id': f'CON-{index:03d}', 'evidence': evidence}))
    return validate_findings(ConsistencyAnalysisResult(findings=findings, summary=candidate.summary), extraction)


def analyze_consistency(extraction_result, sections=None):
    print('\n[AGENT 3] Consistency Analyst 2.0 started')
    requirements = extraction_result.requirements
    if not requirements or len({r.id for r in requirements}) != len(requirements):
        raise ValueError('Empty extraction or duplicate IDs')
    instruction = PROMPT_PATH.read_text(encoding='utf-8')
    base = {'requirements': extraction_result.model_dump(mode='json')['requirements'],
            'source_context': sections or []}
    passes = [('global', 'Compare obligations, scope, exceptions, definitions, dependencies, and duplicates.'),
              ('scenario', 'Construct concrete counterexamples: boundary and temporal cases, repeated events, state transitions, acceptance-test outcomes, and API contract behavior. Check the same conditions and all exceptions. Look independently for missed conflicts.')]
    all_findings = []
    pass_summaries = []
    seen = set()
    for name, focus in passes:
        print(f'[CONSISTENCY PASS] {name}')
        payload = {**base, 'pass': name, 'focus': focus}
        last_error = None
        for attempt in range(1, MAX_ATTEMPTS+1):
            print(f'[AGENT 3] Validation attempt {attempt}/{MAX_ATTEMPTS}')
            response = ask_gemini(prompt=json.dumps(payload, ensure_ascii=False),
                system_instruction=instruction, model='gemini-3.5-flash-lite',
                response_schema=ConsistencyCandidateResult)
            try:
                candidate = ConsistencyCandidateResult.model_validate_json(clean_response(response))
                result = materialize(candidate, extraction_result)
                break
            except (ValueError, TypeError) as error:
                last_error = error
                print(f'[CONSISTENCY CORRECTION] {type(error).__name__}')
                payload['correction'] = {'error': str(error), 'instruction':
                    'Return the complete corrected findings. Keep supported findings and use only real IDs.'}
        else:
            raise RuntimeError(f'Consistency pass {name} failed validation.') from last_error
        pass_summaries.append(f"{name}: {result.summary}")
        for f in result.findings:
            # Merge exact repeated descriptions only; the Auditor handles semantic duplicates.
            key = (f.category, tuple(sorted(f.requirement_ids)),
                   ' '.join(f.title.casefold().split()), ' '.join(f.explanation.casefold().split()))
            if key not in seen:
                seen.add(key)
                all_findings.append(f.model_copy(update={'id': f'CON-{len(all_findings)+1:03d}'}))
        print(f'[CONSISTENCY PASS] {name}: {len(result.findings)} candidates')
    summary = (f'Выполнены общий и сценарный анализы. Сохранено {len(all_findings)} '
               'кандидатов замечаний до аудита. Совпадающие по смыслу замечания могут потребовать объединения. '
               'Комментарии AI (не отдельные подтверждённые замечания): ' + ' | '.join(pass_summaries))
    result = validate_findings(ConsistencyAnalysisResult(findings=all_findings, summary=summary), extraction_result)
    print(f'[AGENT 3] Found {len(result.findings)} consistency issues')
    print('[VALIDATOR] Evidence validation passed')
    return result


def main():
    from core.document_reader import read_document
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, default=BASE_DIR/'reports'/'extracted_requirements.json')
    parser.add_argument('--document', nargs='+', type=Path)
    parser.add_argument('--output', type=Path, default=BASE_DIR/'reports'/'consistency_analysis.json')
    args = parser.parse_args()
    extraction = ExtractionResult.model_validate_json(args.input.read_text(encoding='utf-8'))
    sections = [s for p in args.document or [] for s in read_document(p)]
    if sections:
        for r in extraction.requirements:
            if not any(s['source']==r.source and s.get('page')==r.page and s.get('paragraph')==r.paragraph
                       and ' '.join(r.text.split()) in ' '.join(s['text'].split()) for s in sections):
                raise ValueError(f'Document does not match extraction: {r.id}')
    result = analyze_consistency(extraction, sections)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(result.model_dump_json(indent=2), encoding='utf-8')
    for f in result.findings:
        print(f'{f.id} [{f.severity}] {f.title}: {", ".join(f.requirement_ids)}')
        print(f.explanation)
    print(f'[SAVED] {args.output}')


if __name__ == '__main__':
    main()
