from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from app.harness.registry import ToolRegistry
from app.workflow.nodes import WorkflowNodeHandler, create_node
from app.workflow.state import NODE_NAMES, WorkflowState


def route_after_test(state: WorkflowState) -> str:
    if state["status"] == "FAILED":
        return END
    return "REVIEW" if state["test_result"]["success"] else "FIX"


def build_graph(handler: WorkflowNodeHandler, tools: ToolRegistry, checkpointer: BaseCheckpointSaver, timeout: float = 30.0):
    builder = StateGraph(WorkflowState)
    for name in NODE_NAMES:
        builder.add_node(name, create_node(name, handler, tools, timeout))
    builder.add_edge(START, "ANALYZE")
    for source, target in [("ANALYZE", "PLAN"), ("PLAN", "EXECUTE"), ("EXECUTE", "TEST"), ("FIX", "TEST")]:
        # Failed nodes terminate; successful nodes follow the explicit workflow edges.
        def route(state: WorkflowState, destination=target):
            return END if state["status"] == "FAILED" else destination

        builder.add_conditional_edges(source, route, {END: END, target: target})
    builder.add_conditional_edges("TEST", route_after_test, {END: END, "FIX": "FIX", "REVIEW": "REVIEW"})
    builder.add_edge("REVIEW", END)
    return builder.compile(checkpointer=checkpointer)
