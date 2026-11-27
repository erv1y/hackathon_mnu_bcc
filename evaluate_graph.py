"""Benchmark LangGraph or rescore saved diagnostics without model requests."""
import argparse
import json
import hashlib
import sys
from tempfile import mkdtemp
from core import llm_client
from core.run_metadata import revision
from agents.auditor import audit_models, audit_batch_size
from pathlib import Path

from analyzer import get_settings
from audit_profile import DataModel, read_data
from core.llm_client import API_KEY
from evaluation import CriterionID, EvalCase, ExpectedFinding
from graph_diagnostics import aggregate, diagnostic_case
from web_engine import analyze


class GraphLabel(ExpectedFinding):
    description: str = ""
    acceptable_classifications: list[CriterionID] = []
    severity_rationale: str = ""


class GraphCase(EvalCase):
    expected: list[GraphLabel]
    scope: str = ""
    split: str = "development"
    companion: str = ""
    reference_requirements: list[str]


class GraphSuite(DataModel):
    cases: list[GraphCase]
    version: str = "legacy-1"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path(__file__).parent / "evaluation/graph-synthetic.json")
    parser.add_argument("--check-data", action="store_true")
    parser.add_argument("--mode", choices=("quick", "deep"), default="deep")
    parser.add_argument("--case", help="Evaluate only this scenario ID")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--diagnostics-dir", type=Path)
    parser.add_argument("--resume", action="store_true", help="Reuse compatible complete or partial records in --diagnostics-dir")
    parser.add_argument("--rescore", type=Path, help="Directory of previously saved case diagnostics")
    parser.add_argument("--expert-decisions", type=Path, help="JSON judgments keyed by case ID and expected:prediction indices")
    args = parser.parse_args()
    if args.rescore and args.case:
        parser.error("--case is only available with a dataset.")
    if args.rescore and args.check_data:
        parser.error("Choose --rescore or --check-data.")
    if args.resume and (args.rescore or not args.diagnostics_dir):
        parser.error("--resume requires --diagnostics-dir and a live dataset.")
    failures = []
    not_started = []
    judgments = json.loads(args.expert_decisions.read_text(encoding="utf-8")) if args.expert_decisions else {}
    measured = []
    if args.rescore:
        saved = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(args.rescore.glob("*.case.json"))]
        manifest = args.rescore / "benchmark-plan.json"
        if manifest.exists():
            plan = json.loads(manifest.read_text(encoding="utf-8"))
            planned_by_id = {row["scenario"]["id"]: row for row in plan}
            for record in saved:
                expected = planned_by_id.get(record["scenario"]["id"])
                if not expected or record.get("compatibility") != expected["compatibility"]:
                    parser.error("Saved case conflicts with benchmark plan.")
            saved_ids = {record["scenario"]["id"] for record in saved}
            not_started = [row["scenario"] for case_id, row in planned_by_id.items() if case_id not in saved_ids]
        if not saved or any(record.get("format_version") != 1 for record in saved):
            parser.error("Expected version 1 diagnostic case files.")
        modes = {record["mode"] for record in saved}
        versions = {record["dataset_version"] for record in saved}
        ids = [record["scenario"]["id"] for record in saved]
        revisions = {json.dumps(record.get("compatibility", {}).get("engine"), sort_keys=True)
                     for record in saved}
        if len(revisions) != 1:
            parser.error("Saved results have different engine revisions.")
        if len(modes) != 1 or len(versions) != 1 or len(set(ids)) != len(ids):
            parser.error("Diagnostic paths must contain one dataset and mode with unique case IDs.")
        mode, version = modes.pop(), versions.pop()
        if set(judgments) - set(ids):
            parser.error("Expert decisions contain unknown case IDs.")
        for record in saved:
            if record.get("failure"):
                failures.append(record)
                continue
            case = GraphCase.model_validate(record["scenario"])
            measured.append(diagnostic_case(case, record["report"],
                                            judgments.get(case.id, record["expert_decisions"])))
    else:
        suite = read_data(args.dataset, GraphSuite)
        mode, version = args.mode, suite.version
        if args.case:
            suite.cases = [case for case in suite.cases if case.id == args.case]
            if not suite.cases:
                parser.error("Unknown case ID.")
        ids = {case.id for case in suite.cases}
        if not ids or len(ids) != len(suite.cases):
            parser.error("The dataset must contain unique case IDs.")
        if set(judgments) - ids:
            parser.error("Expert decisions contain unknown case IDs.")
        for case in suite.cases:
            examples = (case.text, case.companion)
            anchors = [anchor for finding in case.expected for anchor in finding.anchors]
            if not all(any(anchor in text for text in examples) for anchor in [*case.reference_requirements, *anchors]):
                parser.error(f"{case.id}: an annotation is absent from the synthetic sources.")
            if version != "legacy-1" and (
                case.split not in {"development", "held-out"} or not case.scope.strip()
                or any(not (label.description.strip() and label.severity_rationale.strip()
                            and label.acceptable_classifications) for label in case.expected)
            ):
                parser.error(f"{case.id}: incomplete diagnostic annotations.")
        if args.check_data:
            print(f"Validated {len(suite.cases)} synthetic cases. No model evaluations executed.")
            return
        settings = get_settings(live=False)
        engine = revision()
        if args.resume and (engine.get("dirty") or not engine.get("commit")):
            parser.error("Resume requires a clean, identifiable engine revision.")
        config = {
            "profile": settings.profile.fingerprint,
            "model": llm_client.MODEL_NAME, "fallbacks": llm_client.FALLBACK_MODELS,
            "retry_rounds": llm_client.MAX_ROUNDS, "request_budget": llm_client.REQUEST_BUDGET,
            "call_timeout": llm_client.CALL_TIMEOUT, "concurrency": llm_client.CONCURRENCY,
            "audit_models": list(audit_models()), "batch_size": audit_batch_size(),
        }
        directory = args.diagnostics_dir or Path(mkdtemp(prefix="graph-benchmark-"))
        print(f"Diagnostics: {directory}", file=sys.stderr)
        pending = []
        plan = []
        expected_paths = {case.id + ".case.json" for case in suite.cases}
        if any(path.name not in expected_paths for path in directory.glob("*.case.json")):
            parser.error("Diagnostics directory contains cases outside the selected suite.")
        for case in suite.cases:
            path = directory / (case.id + ".case.json")
            signature = compatibility(case, mode, version, engine, config)
            plan.append({"scenario": case.model_dump(mode="json"), "compatibility": signature})
            if path.exists():
                if not args.resume:
                    parser.error("Diagnostics directory already contains results. Use --resume or a fresh directory.")
                record = json.loads(path.read_text(encoding="utf-8"))
                if record.get("compatibility") != signature:
                    parser.error(f"{case.id}: saved result is incompatible.")
                if not record.get("failure"):
                    rescored = diagnostic_case(case, record["report"],
                                                judgments.get(case.id, record["expert_decisions"]))
                    measured.append(dict(rescored, compatibility=signature))
                    print(f"[{len(measured)}/{len(suite.cases)}] {case.id} reused", file=sys.stderr)
                    continue
            pending.append((case, signature))
        plan_path = directory / "benchmark-plan.json"
        if plan_path.exists() and json.loads(plan_path.read_text(encoding="utf-8")) != plan:
            parser.error("Benchmark plan differs from the saved selection or configuration.")
        if pending and not API_KEY:
            parser.error("Set GEMINI_API_KEY to execute pending cases.")
        if not plan_path.exists():
            directory.mkdir(parents=True, exist_ok=True)
            temporary = plan_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(plan_path)
        for index, (case, signature) in enumerate(pending):
            print(f"[{len(measured) + len(failures)}/{len(suite.cases)} completed] {case.id}", file=sys.stderr)
            files = [("first.md", case.text.encode(), "main")]
            if case.companion:
                files.append(("second.md", case.companion.encode(), "related"))
            context = "\n\n".join(filter(None, (case.context, f"Declared scope: {case.scope}" if case.scope else "")))
            try:
                report = analyze(files, settings, case.language, mode, context, 60000, lambda _: None)
                record = diagnostic_case(case, report, judgments.get(case.id))
            except KeyboardInterrupt:
                not_started = [item.model_dump(mode="json") for item, _ in pending[index + 1:]]
                record = {"scenario": case.model_dump(mode="json"), "failure": {"code": "interrupted"}}
            except Exception as error:
                code = "provider_failure" if isinstance(error, llm_client.GeminiRequestError) else "analysis_failure"
                record = {"scenario": case.model_dump(mode="json"),
                          "failure": {"code": code, "exception": type(error).__name__}}
            record["compatibility"] = signature
            save_case(directory, record, mode, version)
            if "failure" in record:
                failures.append(record)
                if record["failure"]["code"] == "interrupted":
                    break
            else:
                measured.append(record)
    if args.rescore and args.diagnostics_dir:
        for record in measured:
            save_case(args.diagnostics_dir, record, mode, version)
    metrics, diagnostic = aggregate(measured)
    output = {"engine": "LangGraph", "type": "offline_rescore" if args.rescore else "live", "mode": mode,
              "dataset_version": version, "case_count": len(measured), "metrics": metrics,
              "diagnostic_metrics": diagnostic, "cases": [record["strict"] for record in measured]}
    missing = sum(len(row["scenario"]["expected"]) for row in failures) + sum(len(row["expected"]) for row in not_started)
    counts = diagnostic["counts"]
    expected_total = counts.get("expected", 0) + missing
    diagnostic["full_suite_semantic_recall"] = (
        round(counts.get("matched", 0) / expected_total, 4) if expected_total else None)
    audited = diagnostic["independently_audited_semantic_detection"]
    audited["expected"] += missing
    audited["recall"] = round(audited["matched"] / audited["expected"], 4) if audited["expected"] else None
    denominator = audited["predictions"] + audited["expected"]
    audited["f1"] = round(2 * audited["matched"] / denominator, 4) if denominator else None
    planned = len(measured) + len(failures) + len(not_started)
    completed = sum(record["end_to_end_success"] for record in measured)
    output["run_counts"] = {"planned": planned, "attempted": planned - len(not_started),
                            "completed": completed, "partial": len(measured) - completed,
                            "failed": len(failures), "not_started": len(not_started)}
    output["failures"] = [{"id": row["scenario"]["id"], **row["failure"]} for row in failures]
    output["not_started"] = [row["id"] for row in not_started]
    diagnostic["end_to_end_success_rate"] = round(completed / planned, 4) if planned else None
    if mode == "deep" and (failures or not_started) and planned:
        diagnostic["incomplete_audit_rate"] = round(
            (planned - sum(record["independently_audited"] for record in measured)) / planned, 4)
    planned_agents = sum(record["strict"]["agents_total"] for record in measured)
    planned_agents += (4 if mode == "deep" else 1) * (len(failures) + len(not_started))
    observed_agents = sum(record["strict"]["agents_completed"] for record in measured)
    diagnostic["full_suite_agent_reliability_lower_bound"] = (
        round(observed_agents / planned_agents, 4) if planned_agents else None)
    splits = {record["scenario"]["split"] for record in measured}
    if len(splits) > 1:
        output["splits"] = {}
        for split in sorted(splits):
            subset = [record for record in measured if record["scenario"]["split"] == split]
            strict, diagnostics = aggregate(subset)
            output["splits"][split] = {"case_count": len(subset), "metrics": strict,
                                       "diagnostic_metrics": diagnostics}
    encoded = json.dumps(output, indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


def compatibility(case, mode, version, engine, config):
    payload = dict(scenario=case.model_dump(mode="json"), mode=mode,
                   dataset_version=version, engine=engine, configuration=config)
    normalized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return {"sha256": hashlib.sha256(normalized.encode()).hexdigest(), "engine": engine,
            "configuration": config}


def save_case(directory, record, mode, version):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (record["scenario"]["id"] + ".case.json")
    payload = {**record, "format_version": 1, "mode": mode, "dataset_version": version}
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


if __name__ == "__main__":
    main()
