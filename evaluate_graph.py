"""Evaluate live LangGraph runs using the Claude finding matcher and synthetic labels."""
import argparse
import json
from pathlib import Path

from analyzer import get_settings
from audit_profile import DataModel, read_data
from core.llm_client import API_KEY
from evaluation import EvalCase, measure_case, summarize
from web_engine import analyze


class GraphCase(EvalCase):
    companion: str = ""
    reference_requirements: list[str]


class GraphSuite(DataModel):
    cases: list[GraphCase]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path(__file__).parent / "evaluation/graph-synthetic.json")
    parser.add_argument("--check-data", action="store_true")
    parser.add_argument("--mode", choices=("quick", "deep"), default="deep")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    suite = read_data(args.dataset, GraphSuite)
    if not suite.cases:
        parser.error("The evaluation data is empty.")
    for case in suite.cases:
        examples = (case.text, case.companion)
        anchors = [anchor for finding in case.expected for anchor in finding.anchors]
        if not all(any(anchor in text for text in examples) for anchor in [*case.reference_requirements, *anchors]):
            parser.error(f"{case.id}: an annotation is absent from the synthetic sources.")
    if args.check_data:
        print(f"Validated {len(suite.cases)} synthetic cases. No model evaluations executed.")
        return
    if not API_KEY:
        parser.error("Set GEMINI_API_KEY to run this live evaluation.")
    settings = get_settings(live=False)
    measured = []
    for case in suite.cases:
        files = [("first.md", case.text.encode(), "main")]
        if case.companion:
            files.append(("second.md", case.companion.encode(), "related"))
        report = analyze(files, settings, case.language, args.mode, case.context, 60000, lambda _: None)
        # Review candidates are excluded from measured detections.
        for module in report["modules"]:
            module["defects"] = [item for item in module["defects"] if item["review_reason"] is None]
        details = measure_case(case, report)
        extracted = [" ".join(row["text"].split()) for row in report["native"]["extraction"]["requirements"]]
        found = sum(any(" ".join(quote.split()) in row for row in extracted)
                    for quote in case.reference_requirements)
        passport = report["native"]["passport"]
        details.update(
            false_positives=details["predicted"] - details["matched"],
            false_negatives=details["expected"] - details["matched"],
            extraction_matched=found, extraction_expected=len(case.reference_requirements),
            api_attempts=passport["api_attempts"], tokens=passport["reported_tokens"],
        )
        measured.append(details)
    totals = {key: sum(case[key] for case in measured) for key in
              ("false_positives", "false_negatives", "extraction_matched", "extraction_expected", "api_attempts")}
    expected = totals["extraction_expected"]
    output = {"engine": "LangGraph", "type": "live", "mode": args.mode, "case_count": len(measured),
              "metrics": {**summarize(measured), **totals,
                          "extraction_coverage": totals["extraction_matched"] / expected if expected else None,
                          "execution_seconds": round(sum(case["elapsed_sec"] for case in measured), 2)},
              "cases": measured}
    encoded = json.dumps(output, indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
