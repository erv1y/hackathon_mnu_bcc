
import time

from pathlib import Path

from core.document_reader import read_document

from agents.quick_validator import quick_validate


BASE_DIR = Path(__file__).resolve().parent

REPORTS_DIR = BASE_DIR / "reports"

REPORTS_DIR.mkdir(exist_ok=True)


documents = [

    BASE_DIR / "docs" / "01_transfer_spec.md",

    BASE_DIR / "docs" / "02_business_rules.md"
]


sections = []

for document in documents:

    sections.extend(
        read_document(document)
    )


print("=" * 50)

print("QUICK VALIDATION TEST")

print("=" * 50)

started = time.perf_counter()

result = quick_validate(sections)

elapsed = time.perf_counter() - started


output_path = (
    REPORTS_DIR / "quick_validation.json"
)

output_path.write_text(

    result.model_dump_json(indent=2),

    encoding="utf-8"
)


print("\n" + "=" * 50)

print("QUICK VALIDATION SUMMARY")

print("=" * 50)

print(f"Documents: {len(documents)}")

print(
    f"Requirements: "
    f"{len(result.extraction.requirements)}"
)

print(
    f"Consistency issues: "
    f"{len(result.consistency.findings)}"
)

print(f"Execution time: {elapsed:.2f} seconds")


for finding in result.consistency.findings:

    print("\nIssue:", finding.title)

    print("Category:", finding.category)

    print("Severity:", finding.severity)

    print("Requirements:", finding.requirement_ids)

    print("Explanation:", finding.explanation)


print("\n[SAVED]", output_path)

print("[COMPLETED] Quick Validation")
