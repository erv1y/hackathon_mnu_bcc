
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TypedDict
from collections import Counter

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver


class TestState(TypedDict, total=False):
    document: str
    extraction: str
    quality: str
    consistency: str
    audit: str


calls = Counter()

simulate_failure = True


def extractor(state: TestState):

    calls["extractor"] += 1

    print("[NODE 1] Extractor executed")

    return {
        "extraction": "18 requirements"
    }


def quality_analyst(state: TestState):

    calls["quality"] += 1

    print("[NODE 2] Quality Analyst executed")

    return {
        "quality": "8 quality issues"
    }


def consistency_analyst(state: TestState):

    calls["consistency"] += 1

    print("[NODE 3] Consistency Analyst executed")

    return {
        "consistency": "5 consistency issues"
    }


def auditor(state: TestState):

    calls["auditor"] += 1

    print("[NODE 4] Auditor executed")

    if simulate_failure:

        print("[SIMULATION] API Error 503")

        raise RuntimeError("Simulated API failure")

    print("[AUDITOR] Validation completed")

    return {
        "audit": "Validation completed"
    }


def build_test_graph(checkpointer):

    builder = StateGraph(TestState)

    builder.add_node("extractor", extractor)

    builder.add_node("quality", quality_analyst)

    builder.add_node(
        "consistency",
        consistency_analyst
    )

    builder.add_node("auditor", auditor)

    builder.add_edge(START, "extractor")

    builder.add_edge("extractor", "quality")

    builder.add_edge(
        "extractor",
        "consistency"
    )

    builder.add_edge(
        ["quality", "consistency"],
        "auditor"
    )

    builder.add_edge("auditor", END)

    return builder.compile(
        checkpointer=checkpointer
    )


config = {
    "configurable": {
        "thread_id": "test-resume-001"
    }
}


with TemporaryDirectory() as temp_dir:

    database_path = str(
        Path(temp_dir) / "test_checkpoints.db"
    )

    print("\n" + "=" * 50)
    print("FIRST RUN")
    print("=" * 50)

    with SqliteSaver.from_conn_string(
        database_path
    ) as checkpointer:

        graph = build_test_graph(
            checkpointer
        )

        try:

            graph.invoke(
                {"document": "test.pdf"},
                config
            )

        except RuntimeError as error:

            if str(error) != "Simulated API failure":
                raise

            print(f"[EXPECTED ERROR] {error}")

        snapshot = graph.get_state(config)

        print("\nSaved state:")
        print(snapshot.values)

        print("\nNext nodes:")
        print(snapshot.next)

        assert snapshot.next == ("auditor",)


    # Database connection is closed.
    # Reopen it to test persistent recovery.

    simulate_failure = False

    print("\n" + "=" * 50)
    print("SECOND RUN - RESUME")
    print("=" * 50)

    with SqliteSaver.from_conn_string(
        database_path
    ) as checkpointer:

        graph = build_test_graph(
            checkpointer
        )

        result = graph.invoke(
            None,
            config
        )

        print("\nFINAL RESULT:")
        print(result)


    print("\n" + "=" * 50)
    print("EXECUTION STATISTICS")
    print("=" * 50)

    for agent in (
        "extractor",
        "quality",
        "consistency",
        "auditor"
    ):

        print(f"{agent}: {calls[agent]}")


    assert calls["extractor"] == 1
    assert calls["quality"] == 1
    assert calls["consistency"] == 1
    assert calls["auditor"] == 2

    assert result["audit"] == (
        "Validation completed"
    )

    print("\n[TEST PASSED] Checkpoint & Resume works")
