from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from app.harness.registry import ToolRegistry
from app.workflow.nodes import WorkflowNodeHandler, create_node, create_approval_node
from app.workflow.state import NODE_NAMES, WorkflowState


def route_after_test(state: WorkflowState) -> str:
    if state["status"] == "FAILED":
        return END
    return "REVIEW" if state["test_result"]["success"] else "FIX"


def build_graph(handler: WorkflowNodeHandler, tools: ToolRegistry, checkpointer: BaseCheckpointSaver, timeout: float = 30.0):
    builder = StateGraph(WorkflowState)
    for name in NODE_NAMES:
        builder.add_node(name, create_node(name, handler, tools, timeout))
    builder.add_node("APPROVAL", create_approval_node(handler))
    builder.add_edge(START, "ANALYZE")
    for source, target in [("ANALYZE", "PLAN"), ("PLAN", "EXECUTE"), ("EXECUTE", "TEST"), ("FIX", "TEST")]:
        # Failed nodes terminate; successful nodes follow the explicit workflow edges.
        def route(state: WorkflowState, destination=target):
            if state.get("pending_approval_id"):
                return "APPROVAL"
            return END if state["status"] == "FAILED" else destination

        builder.add_conditional_edges(source, route, {END: END, target: target, "APPROVAL": "APPROVAL"})
    builder.add_conditional_edges("TEST", route_after_test, {END: END, "FIX": "FIX", "REVIEW": "REVIEW"})
    def review_route(state):
        return "FIX" if state["status"] != "FAILED" and state.get("review_decision") == "NEEDS_FIX" else END

    builder.add_conditional_edges("REVIEW", review_route, {END: END, "FIX": "FIX"})
    builder.add_conditional_edges("APPROVAL", lambda state: END if state["status"] == "FAILED" else "TEST", {END: END, "TEST": "TEST"})
    return builder.compile(checkpointer=checkpointer)
