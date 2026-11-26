
import os
import time
import random
from core.run_metadata import record_model
from pathlib import Path
from dotenv import load_dotenv
from google import genai
from google.genai import types, errors


BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

API_KEY = os.getenv("GEMINI_API_KEY")

MODEL_NAME = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.5-flash-lite"
)

FALLBACK_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.6-flash",
    "gemini-3.1-flash-lite"
]

MAX_ROUNDS = 2

RETRYABLE_ERRORS = {
    429, 500, 502, 503, 504
}


def ask_gemini(
    prompt,
    system_instruction=None,
    model=None,
    response_schema=None
):

    if not API_KEY:
        raise ValueError(
            "API key not found in .env"
        )

    models = list(dict.fromkeys([
        model or MODEL_NAME,
        MODEL_NAME,
        *FALLBACK_MODELS
    ]))

    unavailable_models = set()

    last_error = None

    config_data = {
        "system_instruction": system_instruction,
        "temperature": 0.2
    }

    if response_schema is not None:

        config_data["response_mime_type"] = (
            "application/json"
        )

        config_data["response_schema"] = (
            response_schema
        )

        print(
            "[STRUCTURED OUTPUT] "
            f"{response_schema.__name__}"
        )

    with genai.Client(
        api_key=API_KEY,
        http_options=types.HttpOptions(
            timeout=60000,
            retry_options=types.HttpRetryOptions(
                attempts=1
            )
        )
    ) as client:

        for round_number in range(MAX_ROUNDS):

            print(
                f"[API] Round "
                f"{round_number + 1}/{MAX_ROUNDS}"
            )

            for current_model in models:

                if current_model in unavailable_models:
                    continue

                started = time.perf_counter()

                try:

                    print(
                        f"[AI] Using {current_model}"
                    )

                    response = (
                        client.models.generate_content(
                            model=current_model,
                            contents=prompt,
                            config=(
                                types.GenerateContentConfig(
                                    **config_data
                                )
                            )
                        )
                    )

                    if not response.text:

                        last_error = RuntimeError(
                            "Empty AI response"
                        )

                        print(
                            "[FAILED] Empty response"
                        )

                        continue

                    elapsed = (
                        time.perf_counter() - started
                    )

                    print(
                        f"[SUCCESS] {current_model} "
                        f"({elapsed:.2f} seconds)"
                    )
                    record_model(current_model, getattr(response, "model_version", None))
                    return response.text

                except errors.APIError as error:

                    last_error = error

                    code = error.code

                    print(
                        f"[FAILED] "
                        f"{current_model}: {code}"
                    )

                    if code == 404:

                        unavailable_models.add(
                            current_model
                        )

                        continue

                    if code not in RETRYABLE_ERRORS:
                        raise

                    print(
                        "[FALLBACK] Trying next model"
                    )

                except TimeoutError as error:

                    last_error = error

                    print(
                        f"[TIMEOUT] {current_model}"
                    )

            if len(unavailable_models) == len(models):
                break

            if round_number < MAX_ROUNDS - 1:

                delay = (
                    15 * (2 ** round_number)
                    + random.uniform(0, 2)
                )

                print(
                    f"[RETRY] Waiting "
                    f"{delay:.1f} seconds"
                )

                time.sleep(delay)

    raise RuntimeError(
        "All AI models are temporarily unavailable. "
        "Check Gemini API quotas and service status."
    ) from last_error


if __name__ == "__main__":

    print("Testing Gemini API...")

    result = ask_gemini(
        prompt="Reply with OK only."
    )

    print(result)
