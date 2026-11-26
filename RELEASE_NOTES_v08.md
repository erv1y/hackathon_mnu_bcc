# Stabilization update — AI Requirements Validator v0.8

Copy the archive into the existing project, preserving directory structure. This is a patch for the installed Auditor 2.0 update, not a standalone application. Do not copy a replacement `.env`. No new dependencies. Existing modules, prompts, tests and benchmark remain unchanged except the explicitly replaced files listed below.

## One-command replay after extracting

From the activated project directory: `python release_check.py`

This reads `docs/requirements.md` and the saved `reports/extraction_v08.json`, `quality_v08.json`, `direct_v08.json` (or reconstructs its candidates from the audit trail if missing), `audit_v08.json` and `audit_trace_v08.json`. It verifies matching source anchors and candidate registries before writing `reports/stable_replay/`. No model requests are made. Existing output files are not overwritten outside that replay folder. This is a historical replay, not a fresh model run.

## Changes

- `core/release_review.py`: two narrowly scoped, deterministic review checks for explicitly worded Russian purchase-month and threshold-change notification rules. No hard-coded requirement IDs, but linguistic/domain templates are limited and were motivated by known BCC defects. Explicit exceptions in the supported forms suppress the alert. Both alerts must be confirmed by a human. No extra API requests.
- Merged findings preserve members' validated citations in the representative. Authenticity is not semantic relevance. Original decisions are retained in `release_review.json` as `pre_review_audit` and the extended audit trace retains originals.
- `orchestrator.py`: calls this post-audit pass in Deep Mode, validates resulting registries, exports diagnostic JSON, increments cache version. Quick Mode unchanged. Existing direct AI counterexample stage is unchanged.
- `core/knowledge_graph.py`: prefer final audit evidence, including restored quotations.
- `core/advanced_report.py`: add explicit final-evidence appendix. The older base report generator is unchanged.
- `release_check.py`: offline replay; `test_release_review.py`: nine offline test groups.

## Local validation

Actual historical BCC source and saved extraction (49 requirements), quality, original audit and trace were processed using the project's actual Pydantic schemas and validators. Result: 2 additional review alerts (month mismatch and unresolved quota priority); 1 restored quotation (RULE-001 -> CON-001, requirement 4); 8 retained findings/alerts, NOT READY. No new model calls. Existing semantic duplicates remain, so 8 is not a unique-defect count. The 201 question is not automatically corrected. Nine new test groups and ten existing auditor test groups passed. Actual auditor-node and final-state validation plus graph construction were exercised with saved model output and mocked unavailable runtime imports. No real Gemini, LangGraph execution, Streamlit or full PDF report run was performed here. Independent synthetic benchmark remains unexecuted; the targeted regression is not proof of general precision or recall.

## Start product

Continue to use `python -m streamlit run app.py`. For a new Deep analysis the existing API calls remain; errors and rate limits are not solved by this offline supplement. Existing benchmark can be run when API is available, but do not tune against its labels. For confidential documents keep reports local and use the existing consent workflow.
