from copy import deepcopy
from uuid import uuid4

from langsmith import tracing_context

from app.core.config import Settings
from app.harness.errors import HarnessError
from app.harness.models import HarnessTask, TraceEventType
from app.harness.registry import ToolRegistry
from app.harness.trace import TraceCollector
from app.workflow.checkpoint import open_checkpointer
from app.workflow.graph import build_graph
from app.workflow.nodes import WorkflowNodeHandler, restore_trace, trace_data
from app.workflow.state import NODE_NAMES, NodeName, WorkflowRunResult, WorkflowState


class WorkflowRunner:
    def __init__(self, handler: WorkflowNodeHandler, tools: ToolRegistry, settings: Settings, node_timeout_seconds: float = 30.0):
        if not 0 < node_timeout_seconds < float("inf"):
            raise HarnessError("invalid_node_timeout")
        self.handler = handler
        self.tools = tools
        self.settings = settings.model_copy(deep=True)
        self.node_timeout_seconds = node_timeout_seconds

    @staticmethod
    def _config(thread_id: str, max_iterations: int) -> dict:
        return {"configurable": {"thread_id": thread_id}, "recursion_limit": 2 * max_iterations + 10}

    @staticmethod
    def _validate(thread_id: str, interrupt_after: NodeName | None) -> None:
        if not isinstance(thread_id, str) or not thread_id.strip() or len(thread_id) > 128:
            raise HarnessError("invalid_thread_id")
        if interrupt_after is not None and interrupt_after not in NODE_NAMES:
            raise HarnessError("invalid_interrupt_node")

    @staticmethod
    def _result(state: WorkflowState) -> WorkflowRunResult:
        return WorkflowRunResult(
            task_id=state["task_id"], run_id=state["run_id"], thread_id=state["thread_id"],
            status=state["status"], output=state["review_result"]["summary"] if state["status"] == "COMPLETED" else None,
            error=state["error"], iterations=state["iteration"],
            trace=restore_trace(state).snapshot(), state=deepcopy(state),
        )

    async def _invoke(self, graph, config, initial: WorkflowState | None, interrupt_after: NodeName | None) -> WorkflowRunResult:
        await graph.ainvoke(initial, config, interrupt_after=[interrupt_after] if interrupt_after else None, durability="sync")
        snapshot = await graph.aget_state(config)
        state = dict(snapshot.values)
        if snapshot.next and state["status"] not in {"COMPLETED", "FAILED"}:
            state["status"] = "INTERRUPTED"
            trace = restore_trace(state)
            trace.record(state["iteration"], TraceEventType.WORKFLOW_INTERRUPTED, "workflow", True, "Paused at node boundary")
            state["trace"] = trace_data(trace)
            # Official update API preserves the next edge from the last completed node.
            await graph.aupdate_state(config, state, as_node=state["phase"])
        return self._result(state)

    async def run(self, task: HarnessTask, *, thread_id: str | None = None, interrupt_after: NodeName | None = None) -> WorkflowRunResult:
        run_id = str(uuid4())
        thread_id = run_id if thread_id is None else thread_id
        self._validate(thread_id, interrupt_after)
        config = self._config(thread_id, self.settings.workflow_max_iterations)
        trace = TraceCollector()
        trace.record(0, TraceEventType.WORKFLOW_STARTED, "workflow", True, "Workflow started")
        state: WorkflowState = {
            "task_id": task.id, "run_id": run_id, "thread_id": thread_id,
            "instruction": task.instruction, "phase": "START", "status": "RUNNING",
            "analysis_result": None, "plan": [], "execution_result": None,
            "test_result": None, "review_result": None, "error": None,
            "iteration": 0, "max_iterations": self.settings.workflow_max_iterations,
            "trace": trace_data(trace),
        }
        with tracing_context(enabled=False):
            async with open_checkpointer(self.settings.checkpoint_database_url) as saver:
                graph = build_graph(self.handler, self.tools, saver, self.node_timeout_seconds)
                if (await graph.aget_state(config)).values:
                    raise HarnessError("thread_already_exists")
                return await self._invoke(graph, config, state, interrupt_after)

    async def resume(self, thread_id: str, *, interrupt_after: NodeName | None = None) -> WorkflowRunResult:
        self._validate(thread_id, interrupt_after)
        config = self._config(thread_id, self.settings.workflow_max_iterations)
        with tracing_context(enabled=False):
            async with open_checkpointer(self.settings.checkpoint_database_url) as saver:
                graph = build_graph(self.handler, self.tools, saver, self.node_timeout_seconds)
                snapshot = await graph.aget_state(config)
                if not snapshot.values:
                    raise HarnessError("checkpoint_not_found")
                state = dict(snapshot.values)
                if state["thread_id"] != thread_id:
                    raise HarnessError("checkpoint_thread_mismatch")
                if state["status"] in {"COMPLETED", "FAILED"}:
                    return self._result(state)
                if state["status"] != "INTERRUPTED" or not snapshot.next:
                    raise HarnessError("checkpoint_not_resumable")
                trace = restore_trace(state)
                trace.record(state["iteration"], TraceEventType.WORKFLOW_RESUMED, "workflow", True, "Workflow resumed")
                state.update(status="RUNNING", trace=trace_data(trace))
                await graph.aupdate_state(config, state, as_node=state["phase"])
                config = self._config(thread_id, state["max_iterations"])
                # None resumes the stored next task; no new START input is supplied.
                return await self._invoke(graph, config, None, interrupt_after)
