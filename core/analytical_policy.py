"""Shared instructions for impact, scope and classification across analytical stages."""

LANGUAGE_NAMES = {"ru": "Russian", "en": "English", "kk": "Kazakh"}

POLICY = """
Use the document purpose, declared scope, related source documents and abstraction
level. Confirm missing behavior only if an obligation in the supplied sources or
an explicitly applicable rule makes it necessary. Otherwise report uncertainty
as a question or potential risk. Keep optional design suggestions out of defects.
An explicit unsafe obligation can be a correctness defect even at a high level.

Calibrate severity by evidenced consequences, scope and relevant preconditions.
Critical requires direct, unacceptable financial-integrity, security, data-loss
or safe-release exposure. High requires a material business conflict,
major operational failure or implementation blocker. Medium covers material
ambiguity, gaps or untestability without demonstrated severe consequences.
Low covers limited-impact terminology, duplication and documentation issues.
For High and Critical give an evidence-linked severity rationale in the
severity_rationale field if present, otherwise in the explanation or rationale.

Use correctness for an explicitly wrong or unsafe obligation. Use contradiction
for incompatible applicable obligations, duplicate for redundant obligations,
and missing_dependency for an unavailable reference essential to implementation.
Use missing_requirement for an absent necessary obligation, completeness for
missing outcomes within a stated obligation, verifiability for missing measurable
acceptance conditions, unambiguity for multiple plausible interpretations, clarity
for difficult wording, and feasibility for demonstrated implementation constraints.
If several apply, select the primary defect and record related criteria if supported.

Recommendations identify the concrete behavior to resolve, responsible role,
constraints and an observable acceptance check. Offer business alternatives as
proposals and identify decisions still required. Never choose an unauthorized
threshold or policy. Safe rewrites preserve the approved business intent.
"""


READINESS_SUMMARY = {
    "en": "Readiness {status}. Confirmed defects {confirmed}. Manual review {manual}. Merged {merged}. Rejected {rejected}. Extraction audit {extraction}. Coverage is limited to the analyzed material.",
    "kk": "Дайындық {status}. Расталған ақаулар {confirmed}. Қолмен тексеру {manual}. Біріктірілгені {merged}. Қабылданбағаны {rejected}. Шығару аудиті {extraction}. Қамту талданған материалмен шектеледі.",
}
