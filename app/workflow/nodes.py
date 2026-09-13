import asyncio
from copy import deepcopy
from typing import Protocol

from app.harness.models import TraceEvent, TraceEventType
from app.harness.registry import ToolRegistry
from app.harness.trace import TraceCollector
from app.workflow.state import NodeName, NodeOutcome, WorkflowState


class WorkflowNodeHandler(Protocol):
    async def handle(
        self, node: NodeName, state: WorkflowState, tools: ToolRegistry,
    ) -> NodeOutcome:
        """Use registered adapters for tool operations; return a bounded summary.

        PLAN must return a nonempty plan. TEST must return tool_result.
        Handler implementations are supplied by callers, never test fakes here.
        """
        ...


def restore_trace(state: WorkflowState) -> TraceCollector:
    return TraceCollector.from_snapshot([TraceEvent.model_validate(event) for event in state["trace"]])


def trace_data(collector: TraceCollector) -> list[dict]:
    return [event.model_dump(mode="json") for event in collector.snapshot()]


def complete_node(current, name, outcome, trace):
    iteration = current["iteration"]
    error = outcome.fatal_error
    success = outcome.success and (outcome.tool_result is None or outcome.tool_result.success)
    if name == "PLAN" and not outcome.plan:
        success, error = False, "node_execution_failed"
    if name == "TEST" and outcome.tool_result is None:
        success, error = False, "node_execution_failed"
    summary = {"success": success, "summary": outcome.summary}
    if name == "ANALYZE":
        current["analysis_result"] = outcome.summary
    elif name == "PLAN":
        current["plan"] = list(outcome.plan)
    elif name in {"EXECUTE", "FIX"}:
        current["execution_result"] = summary
    elif name == "TEST":
        current["test_result"] = summary
    else:
        current["review_result"] = summary
        current["review_decision"] = outcome.decision
        if outcome.decision == "FAILED":
            success, error = False, "review_failed"
    if not success and error is None:
        if name != "TEST":
            error = "node_failed"
        elif iteration >= current["max_iterations"]:
            error = "max_iterations_exceeded"
    if name == "REVIEW" and outcome.decision == "NEEDS_FIX" and iteration >= current["max_iterations"]:
        success, error = False, "max_iterations_exceeded"
    trace.record(iteration, TraceEventType.NODE_COMPLETED, name, success, "Node completed" if success else "Node failed")
    if error:
        current.update(status="FAILED", error=error)
        trace.record(iteration, TraceEventType.WORKFLOW_FAILED, "workflow", False, error)
    elif name == "REVIEW" and outcome.decision != "NEEDS_FIX":
        current["status"] = "COMPLETED"
        trace.record(iteration, TraceEventType.WORKFLOW_COMPLETED, "workflow", True, "Workflow completed")
    current["trace"] = trace_data(trace)
    return current


def create_node(name: NodeName, handler: WorkflowNodeHandler, tools: ToolRegistry, timeout: float):
    async def node(state: WorkflowState) -> dict:
        current = deepcopy(state)
        current.update(phase=name, status="RUNNING")
        if name == "TEST":
            current["iteration"] += 1
        trace = restore_trace(current)
        iteration = current["iteration"]
        trace.record(iteration, TraceEventType.NODE_STARTED, name, True, "Node started")
        current["trace"] = trace_data(trace)
        try:
            outcome = await asyncio.wait_for(handler.handle(name, deepcopy(current), tools), timeout)
            if not isinstance(outcome, NodeOutcome):
                raise ValueError("invalid_node_outcome")
            # Validate handler output even if constructed without validation.
            outcome = NodeOutcome.model_validate(outcome.model_dump(warnings="error"))
            if outcome.events is not None:
                current["trace"] = outcome.events
                trace = restore_trace(current)
            if outcome.pending_approval_id:
                current.update(status="INTERRUPTED", pending_approval_id=outcome.pending_approval_id)
                trace.record(iteration, TraceEventType.WORKFLOW_INTERRUPTED, "workflow", True, "Waiting for human approval")
                current["trace"] = trace_data(trace)
                return current
        except TimeoutError:
            outcome = NodeOutcome(success=False, summary="Node timed out", fatal_error="node_timeout")
        except Exception:
            outcome = NodeOutcome(success=False, summary="Node failed", fatal_error="node_execution_failed")
        return complete_node(current, name, outcome, trace)

    return node


def create_approval_node(handler):
    from langgraph.types import interrupt

    async def approval_node(state):
        # No side effect before interrupt. The original Agent node is never replayed.
        interrupt({"approval_id": state["pending_approval_id"]})
        current = deepcopy(state)
        outcome = await handler.resume_approval(current)
        current.update(status="RUNNING", pending_approval_id=None, trace=outcome.events)
        return complete_node(current, current["phase"], outcome, restore_trace(current))

    return approval_node
