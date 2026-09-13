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


def create_node(name: NodeName, handler: WorkflowNodeHandler, tools: ToolRegistry, timeout: float):
    async def node(state: WorkflowState) -> dict:
        current = deepcopy(state)
        current.update(phase=name, status="RUNNING")
        if name == "TEST":
            current["iteration"] += 1
        trace = restore_trace(current)
        iteration = current["iteration"]
        trace.record(iteration, TraceEventType.NODE_STARTED, name, True, "Node started")
        error = None
        try:
            outcome = await asyncio.wait_for(handler.handle(name, deepcopy(current), tools), timeout)
            if not isinstance(outcome, NodeOutcome):
                raise ValueError("invalid_node_outcome")
            # Validate handler output even if constructed without validation.
            outcome = NodeOutcome.model_validate(outcome.model_dump(warnings="error"))
            if name == "PLAN" and not outcome.plan:
                raise ValueError("plan_required")
            if name == "TEST" and outcome.tool_result is None:
                raise ValueError("test_tool_result_required")
            success = outcome.success and (outcome.tool_result is None or outcome.tool_result.success)
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
            if not success:
                if name != "TEST":
                    error = "node_failed"
                elif iteration >= current["max_iterations"]:
                    error = "max_iterations_exceeded"
        except TimeoutError:
            success, error = False, "node_timeout"
        except Exception:
            success, error = False, "node_execution_failed"
        trace.record(iteration, TraceEventType.NODE_COMPLETED, name, success, "Node completed" if success else "Node failed")
        if error:
            current.update(status="FAILED", error=error)
            trace.record(iteration, TraceEventType.WORKFLOW_FAILED, "workflow", False, error)
        elif name == "REVIEW":
            current["status"] = "COMPLETED"
            trace.record(iteration, TraceEventType.WORKFLOW_COMPLETED, "workflow", True, "Workflow completed")
        current["trace"] = trace_data(trace)
        return current

    return node
