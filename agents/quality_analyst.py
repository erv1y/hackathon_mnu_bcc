"""Context-aware quality review with deterministic evidence anchors."""
import argparse
import json
from pathlib import Path
from collections import Counter
from typing import Literal
from pydantic import BaseModel, Field

from core.llm_client import ask_gemini
from core.models import (ExtractionResult, QualityAnalysisResult,
                         QualityFinding, RequirementAssessment)

BASE_DIR = Path(__file__).resolve().parent.parent
PROMPT_PATH = BASE_DIR / 'prompts' / 'quality_analyst.txt'
MAX_ATTEMPTS = 2
BATCH_SIZE = 20


class QualityCandidate(BaseModel):
    requirement_id: str
    criterion: Literal['correctness','clarity','completeness','verifiability','consistency',
                       'feasibility','necessity','traceability','unambiguity']
    severity: Literal['critical','high','medium','low']
    explanation: str
    severity_rationale: str = ""
    recommendation: str
    confidence: float = Field(ge=0, le=1)


class QualityAssessmentCandidate(BaseModel):
    requirement_id: str
    status: Literal['passed','needs_revision','needs_clarification']
    quality_score: int = Field(ge=0, le=100)
    findings: list[QualityCandidate]


class QualityCandidateResult(BaseModel):
    assessments: list[QualityAssessmentCandidate]


def clean_response(response):
    response = response.strip()
    if response.startswith('```'):
        response = response.removeprefix('```json').removeprefix('```').removesuffix('```')
    return response.strip()


def validate_quality(result, extraction_result):
    requirements = {r.id: r for r in extraction_result.requirements}
    expected = Counter(r.id for r in extraction_result.requirements)
    actual = Counter(a.requirement_id for a in result.assessments)
    if len(requirements) != len(extraction_result.requirements) or expected != actual:
        raise ValueError('Quality assessments do not match the expected requirement IDs.')
    for a in result.assessments:
        if (a.status == 'passed') != (not a.findings):
            raise ValueError(f'{a.requirement_id}: status and findings disagree')
        for f in a.findings:
            if f.requirement_id != a.requirement_id:
                raise ValueError(f'{a.requirement_id}: finding refers to a different ID')
            quote = ' '.join(f.evidence.split())
            if not quote or quote not in ' '.join(requirements[a.requirement_id].text.split()):
                raise ValueError(f'{a.requirement_id}: unsupported evidence')
    return result


def materialize(candidate, targets):
    by_id = {r.id: r for r in targets}
    if Counter(a.requirement_id for a in candidate.assessments) != Counter(r.id for r in targets):
        raise ValueError('Return exactly one assessment per target ID, no other IDs.')
    assessments = []
    for a in candidate.assessments:
        findings = []
        for f in a.findings:
            if f.requirement_id != a.requirement_id:
                raise ValueError(f'{a.requirement_id}: finding refers to a different ID')
            # Full source quotation, NOT an assertion that the interpretation is correct.
            findings.append(QualityFinding.model_validate({**f.model_dump(),
                'evidence': by_id[a.requirement_id].text}))
        assessments.append(RequirementAssessment.model_validate({**a.model_dump(),
            'findings': findings}))
    return validate_quality(QualityAnalysisResult(assessments=assessments),
                            ExtractionResult(requirements=targets))


def analyze_quality(extraction_result, sections=None):
    print('\n[AGENT 2] Quality Analyst 2.0 started')
    requirements = extraction_result.requirements
    if not requirements or len({r.id for r in requirements}) != len(requirements):
        raise ValueError('Empty extraction or duplicate IDs')
    instruction = PROMPT_PATH.read_text(encoding='utf-8')
    assessments = []
    total = (len(requirements) + BATCH_SIZE - 1) // BATCH_SIZE
    for index in range(0, len(requirements), BATCH_SIZE):
        targets = requirements[index:index+BATCH_SIZE]
        payload = {'target_ids': [r.id for r in targets],
                   'requirements': extraction_result.model_dump(mode='json')['requirements'],
                   'source_context': sections or []}
        pending = {r.id: r for r in targets}
        retained = {}
        print(f'[QUALITY BATCH] {index//BATCH_SIZE+1}/{total}')
        for attempt in range(1, MAX_ATTEMPTS+1):
            print(f'[AGENT 2] Validation attempt {attempt}/{MAX_ATTEMPTS}')
            payload['target_ids'] = list(pending)
            response = ask_gemini(prompt=json.dumps(payload, ensure_ascii=False),
                system_instruction=instruction, model='gemini-3.5-flash-lite',
                response_schema=QualityCandidateResult)
            try:
                rows = json.loads(clean_response(response))['assessments']
                if not isinstance(rows, list):
                    raise ValueError('invalid envelope')
            except (ValueError, TypeError, KeyError, RecursionError):
                rows = []
            counts = Counter(row['requirement_id'] for row in rows if isinstance(row, dict)
                             and isinstance(row.get('requirement_id'), str))
            for row in rows:
                rid = row.get('requirement_id') if isinstance(row, dict) else None
                if not isinstance(rid, str) or rid not in pending or counts[rid] != 1:
                    continue
                try:
                    candidate = QualityAssessmentCandidate.model_validate(row)
                    result = materialize(QualityCandidateResult(assessments=[candidate]), [pending[rid]])
                except ValueError:
                    continue
                retained[rid] = result.assessments[0]
                del pending[rid]
            if not pending:
                assessments.extend(retained[r.id] for r in targets)
                break
            print(f'[QUALITY CORRECTION] {len(pending)} missing or invalid assessments')
            payload['correction'] = {'instruction': 'Return complete corrected assessments for the remaining target IDs only.'}
        else:
            raise RuntimeError('Quality Analyst failed validation; no partial result accepted.')
    result = validate_quality(QualityAnalysisResult(assessments=assessments), extraction_result)
    print(f'[AGENT 2] Analyzed {len(result.assessments)} requirements')
    print(f'[AGENT 2] Found {sum(len(a.findings) for a in result.assessments)} quality issues')
    print('[VALIDATOR] Quality evidence and assessment coverage passed')
    return result


def main():
    from core.document_reader import read_document
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, default=BASE_DIR/'reports'/'extracted_requirements.json')
    parser.add_argument('--document', nargs='+', type=Path)
    parser.add_argument('--output', type=Path, default=BASE_DIR/'reports'/'quality_analysis.json')
    args = parser.parse_args()
    extraction = ExtractionResult.model_validate_json(args.input.read_text(encoding='utf-8'))
    sections = [s for p in args.document or [] for s in read_document(p)]
    if sections:
        for r in extraction.requirements:
            if not any(s['source']==r.source and s.get('page')==r.page and s.get('paragraph')==r.paragraph
                       and ' '.join(r.text.split()) in ' '.join(s['text'].split()) for s in sections):
                raise ValueError(f'Document does not match extraction: {r.id}')
    result = analyze_quality(extraction, sections)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(result.model_dump_json(indent=2), encoding='utf-8')
    print(f'[SAVED] {args.output}')


if __name__ == '__main__':
    main()
