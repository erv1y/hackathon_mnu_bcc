
from typing import Literal

from pydantic import BaseModel

from core.llm_client import ask_gemini


class TestResponse(BaseModel):

    status: Literal["passed", "failed"]

    message: str

    severity: Literal[
        "critical",
        "high",
        "medium",
        "low"
    ] | None = None


response = ask_gemini(
    prompt=(
        "Return status passed, message OK "
        "and no severity."
    ),
    response_schema=TestResponse
)

result = TestResponse.model_validate_json(
    response
)

print("\nRAW RESPONSE:")
print(response)

print("\nVALIDATED RESPONSE:")
print(result)

assert result.status == "passed"
assert result.message == "OK"
assert result.severity is None

print("\n[TEST PASSED] Structured Output works")
