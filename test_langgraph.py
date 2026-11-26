
from typing import TypedDict
from langgraph.graph import StateGraph, START, END


class TestState(TypedDict, total=False):

    document: str
    extraction: str
    quality: str
    consistency: str
    audit: str


def extractor(state: TestState):

    print("[NODE 1] Extractor")

    return {
        "extraction": "18 requirements"
    }


def quality_analyst(state: TestState):

    print("[NODE 2] Quality Analyst")

    return {
        "quality": "8 quality issues"
    }


def consistency_analyst(state: TestState):

    print("[NODE 3] Consistency Analyst")

    return {
        "consistency": "5 consistency issues"
    }


def auditor(state: TestState):

    print("[NODE 4] Auditor")

    print(state["extraction"])
    print(state["quality"])
    print(state["consistency"])

    return {
        "audit": "Validation completed"
    }


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

builder.add_edge("extractor", "consistency")

builder.add_edge(
    ["quality", "consistency"],
    "auditor"
)

builder.add_edge("auditor", END)


graph = builder.compile()


result = graph.invoke({
    "document": "test.pdf"
})


print("\nFINAL GRAPH STATE:")

print(result)


assert result["audit"] == "Validation completed"

print("\n[TEST PASSED] LangGraph works")
