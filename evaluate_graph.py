"""Benchmark LangGraph or rescore saved diagnostics without model requests."""
import argparse
import json
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
    parser.add_argument("--rescore", type=Path, help="Directory of previously saved case diagnostics")
    parser.add_argument("--expert-decisions", type=Path, help="JSON judgments keyed by case ID and expected:prediction indices")
    args = parser.parse_args()
    if args.rescore and args.case:
        parser.error("--case is only available with a dataset.")
    if args.rescore and args.check_data:
        parser.error("Choose --rescore or --check-data.")
    judgments = json.loads(args.expert_decisions.read_text(encoding="utf-8")) if args.expert_decisions else {}
    measured = []
    if args.rescore:
        saved = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(args.rescore.glob("*.case.json"))]
        if not saved or any(record.get("format_version") != 1 for record in saved):
            parser.error("Expected version 1 diagnostic case files.")
        modes = {record["mode"] for record in saved}
        versions = {record["dataset_version"] for record in saved}
        ids = [record["scenario"]["id"] for record in saved]
        if len(modes) != 1 or len(versions) != 1 or len(set(ids)) != len(ids):
            parser.error("Diagnostic paths must contain one dataset and mode with unique case IDs.")
        mode, version = modes.pop(), versions.pop()
        if set(judgments) - set(ids):
            parser.error("Expert decisions contain unknown case IDs.")
        for record in saved:
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
        if not API_KEY:
            parser.error("Set GEMINI_API_KEY to run this live evaluation.")
        settings = get_settings(live=False)
        for case in suite.cases:
            files = [("first.md", case.text.encode(), "main")]
            if case.companion:
                files.append(("second.md", case.companion.encode(), "related"))
            report = analyze(files, settings, case.language, mode, case.context, 60000, lambda _: None)
            measured.append(diagnostic_case(case, report, judgments.get(case.id)))
            if args.diagnostics_dir:
                save_case(args.diagnostics_dir, measured[-1], mode, version)
    if args.rescore and args.diagnostics_dir:
        for record in measured:
            save_case(args.diagnostics_dir, record, mode, version)
    metrics, diagnostic = aggregate(measured)
    output = {"engine": "LangGraph", "type": "offline_rescore" if args.rescore else "live", "mode": mode,
              "dataset_version": version, "case_count": len(measured), "metrics": metrics,
              "diagnostic_metrics": diagnostic, "cases": [record["strict"] for record in measured]}
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


def save_case(directory, record, mode, version):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (record["scenario"]["id"] + ".case.json")
    payload = {**record, "format_version": 1, "mode": mode, "dataset_version": version}
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


if __name__ == "__main__":
    main()
