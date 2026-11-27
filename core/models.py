
from typing import Literal

from pydantic import BaseModel, Field


class Requirement(BaseModel):
    id: str

    type: Literal[
        "functional",
        "non_functional",
        "business_rule",
        "acceptance_criterion",
        "other"
    ]

    text: str = Field(min_length=1)
    source: str
    page: int | None = None
    paragraph: int | None = None


class ExtractionResult(BaseModel):
    requirements: list[Requirement]

class QualityFinding(BaseModel):
    requirement_id: str

    criterion: Literal[
        "correctness",
        "clarity",
        "completeness",
        "verifiability",
        "consistency",
        "feasibility",
        "necessity",
        "traceability",
        "unambiguity"
    ]

    severity: Literal[
        "critical",
        "high",
        "medium",
        "low"
    ]

    evidence: str
    explanation: str
    severity_rationale: str = ""
    recommendation: str

    confidence: float = Field(ge=0, le=1)


class RequirementAssessment(BaseModel):
    requirement_id: str

    status: Literal[
        "passed",
        "needs_revision",
        "needs_clarification"
    ]

    quality_score: int = Field(ge=0, le=100)

    findings: list[QualityFinding]


class QualityAnalysisResult(BaseModel):
    assessments: list[RequirementAssessment]

class FindingEvidence(BaseModel):
    requirement_id: str
    quote: str
    source: str
    page: int | None = None
    paragraph: int | None = None


class ConsistencyFinding(BaseModel):
    id: str

    category: Literal[
        "contradiction",
        "duplicate",
        "missing_requirement",
        "inconsistent_constraint",
        "missing_dependency",
        "scope_conflict",
        "terminology_inconsistency"
    ]

    severity: Literal[
        "critical",
        "high",
        "medium",
        "low"
    ]

    requirement_ids: list[str] = Field(min_length=1)

    title: str
    evidence: list[FindingEvidence] = Field(min_length=1)
    explanation: str
    severity_rationale: str = ""
    impact: str
    recommendation: str

    confidence: float = Field(ge=0, le=1)


class ConsistencyAnalysisResult(BaseModel):
    repair_diagnostics: list[dict] = Field(default_factory=list)
    unresolved: list[dict] = Field(default_factory=list)
    findings: list[ConsistencyFinding]
    summary: str

Criterion = Literal["correctness", "clarity", "unambiguity", "completeness", "verifiability",
                    "missing_requirement", "missing_dependency", "contradiction", "duplicate",
                    "feasibility", "consistency", "necessity", "traceability",
                    "inconsistent_constraint", "scope_conflict", "terminology_inconsistency"]


class AuditJudgment(BaseModel):
    finding_id: str

    decision: Literal[
        "confirmed",
        "revised",
        "rejected",
        "merged"
    ]

    final_severity: Literal[
        "critical",
        "high",
        "medium",
        "low"
    ] | None = None

    title: str
    explanation: str
    recommendation: str
    audit_reason: str

    primary_criterion: Criterion | None = None
    related_criteria: list[Criterion] = Field(default_factory=list, max_length=4)
    severity_rationale: str = ""
    assessment_kind: Literal["defect", "question", "potential_risk"] = "defect"
    action_kind: Literal["rewrite", "clarify", "business_decision"] = "clarify"
    merged_into: str | None = None

    confidence: float = Field(ge=0, le=1)

    # Additive fields: older saved audit reports can still be loaded.
    root_cause: str = ""
    scope: str = ""


class AuditDecision(AuditJudgment):
    requirement_ids: list[str]
    final_evidence: list[FindingEvidence] = Field(default_factory=list)


class AuditCandidate(AuditJudgment):
    evidence_ids: list[str] = Field(description="Relevant verified requirement IDs, empty for rejected decisions")


class ExtractionAudit(BaseModel):
    status: Literal[
        "complete",
        "incomplete",
        "uncertain"
    ]

    missing_requirements: list[str]
    unsupported_requirements: list[str]
    comments: str


class AuditSummary(BaseModel):
    extraction_audit: ExtractionAudit
    readiness: Literal["ready", "conditionally_ready", "not_ready"]
    executive_summary: str
    priority_actions: list[str]
    limitations: list[str]


class AuditPatch(BaseModel):
    decisions: list[AuditDecision]


class AuditResult(AuditSummary, AuditPatch):
    pass


class AuditCandidatePatch(BaseModel):
    decisions: list[AuditCandidate]


class AuditCandidateResult(AuditSummary, AuditCandidatePatch):
    pass


class AuditMerge(BaseModel):
    finding_id: str
    merged_into: str
    audit_reason: str


class AuditMergePlan(BaseModel):
    merges: list[AuditMerge]


class ValidationResult(BaseModel):
    source_files: list[str]

    execution_time_seconds: float

    extraction: ExtractionResult

    quality: QualityAnalysisResult

    consistency: ConsistencyAnalysisResult

    audit: AuditResult
