"""Offline coverage of Auditor's provider contract and targeted recovery."""
import json
import unittest
from unittest.mock import MagicMock, patch

from google import genai
from google.genai import _transformers
from google.genai.errors import ClientError

from agents import auditor, quality_analyst
from core import counterexample, llm_client
from core.models import AuditCandidatePatch, AuditCandidateResult, ConsistencyAnalysisResult
from test_auditor_v08 import fixtures


def candidate(row):
    return {**row.model_dump(mode="json", exclude={"requirement_ids", "final_evidence"}),
            "evidence_ids": [item.requirement_id for item in row.final_evidence]}


class EvidenceSelectionTests(unittest.TestCase):
    def setUp(self):
        self.extraction, self.sections, self.quality, self.consistency, self.result = fixtures()
        self.registry = {row.id: row for row in self.extraction.requirements}
        self.originals = {row["id"]: row for row in auditor.prepare_findings(self.quality, self.consistency)}

    def test_sdk_schema_and_registry_materialization(self):
        with genai.Client(api_key="offline-schema-test") as client:
            for schema in (AuditCandidateResult, AuditCandidatePatch):
                _transformers.t_schema(client._api_client, schema)
                fields = schema.model_json_schema()["$defs"]["AuditCandidate"]["properties"]
                self.assertIn("evidence_ids", fields)
                self.assertNotIn("final_evidence", fields)
                self.assertNotIn("requirement_ids", fields)
        envelopes = [candidate(row) for row in self.result.decisions]
        payload = self.result.model_dump(mode="json", exclude={"decisions"}) | {"decisions": envelopes}
        with patch.object(auditor, "ask_gemini", return_value=json.dumps(payload)) as api:
            audited = auditor.audit_results(self.sections, self.extraction, self.quality, self.consistency)
        api.assert_called_once()
        self.assertIs(api.call_args.kwargs["response_schema"], AuditCandidateResult)
        self.assertEqual(audited.decisions, self.result.decisions)

    def test_negative_evidence_selection_and_legacy_validation(self):
        row = self.result.decisions[1]
        fid = row.finding_id
        cases = [
            (candidate(row) | {"evidence_ids": ["R1", "UNKNOWN"]}, "unknown_requirement_ids"),
            (candidate(row) | {"evidence_ids": ["R1", "R1"]}, "duplicate_requirement_ids"),
            (candidate(row) | {"evidence_ids": ["R1", "R2", "R4"]}, "irrelevant_evidence_selection"),
            (candidate(row) | {"evidence_ids": ["R4"], "explanation": "R4 implies an unrelated obligation."},
             "unrelated_evidence_selection"),
            (candidate(row) | {"evidence_ids": ["R1"], "explanation": "R1 conflicts with R2."},
             "missing_contradiction_side"),
            (candidate(row) | {"evidence_ids": []}, "missing_evidence_ids"),
            (candidate(row) | {"explanation": "R1 requires unrelated handling under R4."},
             "unsupported_explanation_reference"),
        ]
        legacy = row.model_dump(mode="json")
        legacy["final_evidence"][0].update(quote="fabricated confidential text", paragraph=999)
        cases.extend([(legacy, "invalid_source_coordinates"), (legacy, "invalid_quotation")])
        for raw, rule in cases:
            with self.subTest(rule=rule):
                accepted, issues = auditor.parse_rows({"decisions": [raw]}, {fid}, self.originals,
                                                      self.registry, set())
                self.assertEqual(accepted, {})
                self.assertIn(rule, {issue.rule for issue in issues})

    def test_two_production_mismatches_and_provider_failure_resume(self):
        source_findings = self.quality.assessments[0].findings
        source_findings.extend(source_findings[0].model_copy() for _ in range(10))
        empty_consistency = ConsistencyAnalysisResult(findings=[], summary="No conflicts found")
        fid9, fid11 = "QUAL-009", "QUAL-011"
        affected = [fid9, fid11]
        template = self.result.decisions[0].model_copy(update={
            "decision": "confirmed", "merged_into": None, "final_severity": "high",
        })
        clean = {f"QUAL-{i:03d}": template.model_copy(update={"finding_id": f"QUAL-{i:03d}"})
                 for i in range(1, 12)}
        broken = [row.model_dump(mode="json") for row in clean.values()]
        for index in (8, 10):
            broken[index]["requirement_ids"] = ["R1", "R3"]
            broken[index]["explanation"] = "Private fixture claim about R3."
        initial = self.result.model_dump(mode="json", exclude={"decisions"}) | {"decisions": broken}
        progress = {}

        def run():
            token = auditor.audit_progress.set(progress)
            try:
                return auditor.audit_results(self.sections, self.extraction, self.quality, empty_consistency)
            finally:
                auditor.audit_progress.reset(token)

        with patch.object(auditor, "audit_batch_size", return_value=12):
            with patch.object(auditor, "ask_gemini", side_effect=[
                json.dumps(initial), llm_client.GeminiRequestError("exhausted fallback", 429)
            ]):
                with self.assertLogs("agents.auditor", level="WARNING") as captured:
                    with self.assertRaises(auditor.AuditorFailure):
                        run()
            self.assertEqual(progress["unresolved_ids"], affected)
            self.assertEqual(progress["remaining_phase"], "finding_review")
            self.assertEqual(len(progress["decisions"]), 9)
            logs = "\n".join(captured.output)
            self.assertNotIn("Private fixture claim", logs)
            for fid in affected:
                self.assertIn(f"unsupported_explanation_reference finding={fid}", logs)
                self.assertIn(f"incomplete_evidence_coverage finding={fid}", logs)
            # Both SQLite and the in-memory graph saver use serializable checkpoint data.
            progress = json.loads(json.dumps(progress))
            repaired = {"decisions": [candidate(clean[fid]) for fid in affected]}
            with patch.object(auditor, "ask_gemini", side_effect=[
                json.dumps(repaired), '{"merges":[]}'
            ]) as api:
                result = run()
            request = json.loads(api.call_args_list[0].kwargs["prompt"])
            self.assertEqual(request["requested_finding_ids"], affected)
            self.assertEqual(len(request["locked_decisions"]), 9)
            for fid in affected:
                context = request["evidence_repairs"][fid]
                self.assertEqual([row["id"] for row in context["verified_sources"]], ["R1", "R3"])
                self.assertIn("missing_evidence_ids", {d["rule"] for d in context["diagnostics"]})
            self.assertEqual(result.decisions, list(clean.values()))
            self.assertEqual(progress["decision_repairs"], {})

    def test_quality_repairs_only_invalid_assessments(self):
        extraction = self.extraction.model_copy(update={"requirements": self.extraction.requirements[:2]})
        rows = [row.model_dump(mode="json") for row in self.quality.assessments[:2]]
        good, other = rows
        bad = dict(good, findings=[dict(good["findings"][0], requirement_id="INVALID")])
        answer = [{"assessments": [bad, other]}, {"assessments": [good, dict(other, quality_score=0)]}]
        with patch.object(quality_analyst, "ask_gemini", side_effect=list(map(json.dumps, answer))) as api:
            result = quality_analyst.analyze_quality(extraction)
        self.assertEqual(json.loads(api.call_args.kwargs["prompt"])["target_ids"], ["R1"])
        self.assertEqual(result.assessments[1].quality_score, other["quality_score"])

    def test_pair_repair_preserves_valid_review_through_timeout(self):
        pairs = [{"pair_id": "PAIR-001", "requirement_ids": ["R1", "R2"]},
                 {"pair_id": "PAIR-002", "requirement_ids": ["R2", "R3"]}]

        def reply(prompt, **_):
            asked = json.loads(prompt)["pairs"]
            if len(asked) == 1:
                self.assertEqual(asked[0]["pair_id"], "PAIR-002")
                raise TimeoutError("fixture")
            reviews = []
            for pair in asked:
                a, b = pair["requirements"]
                reviews.append(dict(pair_id=pair["pair_id"], verdict="not_comparable",
                                    a=dict(requirement_id=a["id"], quote=a["text"], expected_result=""),
                                    b=dict(requirement_id=b["id"],
                                           quote=b["text"] if pair["pair_id"] == "PAIR-001" else "false",
                                           expected_result=""),
                                    rationale="Different scopes", scope="", scenario="", title="", recommendation=""))
            return json.dumps({"reviews": reviews})

        debug = {}
        with patch.object(counterexample, "select_pairs", return_value=pairs), patch.object(
            llm_client, "ask_gemini", side_effect=reply
        ) as api:
            counterexample.analyze_counterexamples(self.extraction, self.consistency, debug=debug)
        self.assertEqual(api.call_count, 2)
        self.assertEqual(debug["reviewed_pair_ids"], ["PAIR-001"])
        self.assertEqual(debug["unresolved_pair_ids"], ["PAIR-002"])
        self.assertFalse(debug["complete"])

    def test_sdk_429_timeout_exhausted_fallback_and_cooldown(self):
        client = MagicMock()
        client.models.generate_content.side_effect = [
            ClientError(429, {"error": {"message": "secret quota detail"}}),
            TimeoutError("secret transport detail"),
            ClientError(429, {"error": {"message": "secret fallback detail"}}),
        ]
        client_factory = MagicMock()
        client_factory.return_value.__enter__.return_value = client
        metrics = []
        token = llm_client.request_metrics.set(metrics)
        self.addCleanup(llm_client.request_metrics.reset, token)
        with patch.multiple(llm_client, API_KEY="offline-key", MODEL_NAME="backup",
                            FALLBACK_MODELS=["last"], MAX_ROUNDS=1, _cooldowns={}), patch(
            "core.llm_client.genai.Client", client_factory
        ):
            with self.assertRaises(llm_client.GeminiRequestError) as error:
                llm_client.ask_gemini("mock", model="primary")
            self.assertEqual(error.exception.code, 429)
            self.assertEqual(set(llm_client._cooldowns), {"primary", "last"})
        self.assertEqual(client.models.generate_content.call_count, 3)
        self.assertEqual([item["status"] for item in metrics],
                         ["provider_error", "transport_error", "provider_error"])
