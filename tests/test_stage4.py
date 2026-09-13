import asyncio
import json

import pytest

from app.core.config import Settings
from app.harness.errors import HarnessError
from app.harness.models import HarnessTask, ToolDefinition, ToolExecutionResult, TraceEventType
from app.harness.registry import ToolRegistry
from app.harness.trace import TraceCollector
from app.workflow.checkpoint import checkpoint_path, open_checkpointer
from app.workflow.graph import build_graph
from app.workflow.runner import WorkflowRunner
from app.workflow.state import NodeOutcome, WorkflowStatus


TASK = HarnessTask(id=42, title="Demo task", instruction="Inspect and verify the task")
NORMAL = ["ANALYZE", "PLAN", "EXECUTE", "TEST", "REVIEW"]
FIX_PATH = ["ANALYZE", "PLAN", "EXECUTE", "TEST", "FIX", "TEST", "REVIEW"]


class FakeTool:
    definition = ToolDefinition(
        name="demo", description="Stage 4 test fixture",
        input_schema={
            "type": "object", "properties": {
                "operation": {"enum": ["EXECUTE", "TEST", "FIX"]},
                "iteration": {"type": "integer"},
            }, "required": ["operation", "iteration"], "additionalProperties": False,
        },
    )

    def __init__(self, passes=(True,), payload=None):
        self.passes = passes
        self.payload = payload
        self.calls = []

    async def execute(self, arguments):
        self.calls.append(dict(arguments))
        success = True
        if arguments["operation"] == "TEST":
            success = self.passes[min(arguments["iteration"] - 1, len(self.passes) - 1)]
        return ToolExecutionResult(success=success, data=self.payload, error=None if success else "test_failed")


class ScriptedHandler:
    def __init__(self, error_at=None):
        self.calls = []
        self.registries = []
        self.states = []
        self.error_at = error_at

    async def handle(self, node, state, tools):
        self.calls.append(node)
        self.registries.append(tools)
        self.states.append(state)
        if node == self.error_at:
            raise RuntimeError("SECRET_EXCEPTION")
        if node == "PLAN":
            return NodeOutcome(summary="Plan prepared", plan=["Inspect workspace", "Run selected tests"])
        if node in {"EXECUTE", "TEST", "FIX"}:
            arguments = {"operation": node, "iteration": state["iteration"]}
            assert tools.validate_arguments("demo", arguments)
            tool_result = await tools.get("demo").execute(arguments)
            return NodeOutcome(summary=f"{node} evaluated", tool_result=tool_result)
        return NodeOutcome(summary=f"{node} complete")


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        checkpoint_database_url=f"sqlite:///{(tmp_path / 'checkpoints.db').as_posix()}",
        workflow_max_iterations=3,
    )


def registry(passes=(True,), payload=None):
    tools = ToolRegistry()
    fake = FakeTool(passes, payload)
    tools.register(fake)
    return tools, fake


def test_normal_flow_and_registry_reuse(settings):
    handler = ScriptedHandler()
    tools, fake = registry()
    result = asyncio.run(WorkflowRunner(handler, tools, settings).run(TASK))
    assert result.status == WorkflowStatus.COMPLETED
    assert result.output == "REVIEW complete" and result.error is None
    assert handler.calls == NORMAL
    assert all(item is tools for item in handler.registries)
    assert [call["operation"] for call in fake.calls] == ["EXECUTE", "TEST"]
    assert result.iterations == 1
    assert result.state["analysis_result"] == "ANALYZE complete"
    assert result.state["plan"] == ["Inspect workspace", "Run selected tests"]


def test_test_failure_routes_fix_then_review(settings):
    handler = ScriptedHandler()
    tools, fake = registry((False, True))
    result = asyncio.run(WorkflowRunner(handler, tools, settings).run(TASK))
    assert handler.calls == FIX_PATH
    assert result.status == WorkflowStatus.COMPLETED and result.iterations == 2
    assert [call["operation"] for call in fake.calls] == ["EXECUTE", "TEST", "FIX", "TEST"]
    fix_state = handler.states[4]
    assert fix_state["test_result"]["success"] is False
    assert result.state["test_result"]["success"] is True


def test_max_iterations_ends_failed(settings):
    tools, _ = registry((False,))
    handler = ScriptedHandler()
    result = asyncio.run(WorkflowRunner(handler, tools, settings).run(TASK))
    assert handler.calls == ["ANALYZE", "PLAN", "EXECUTE", "TEST", "FIX", "TEST", "FIX", "TEST"]
    assert result.status == WorkflowStatus.FAILED
    assert result.error == "max_iterations_exceeded"
    assert result.iterations == 3
    assert result.trace[-1].event_type == TraceEventType.WORKFLOW_FAILED


def test_checkpoint_contains_state_and_execution_position(settings):
    async def scenario():
        tools, _ = registry()
        handler = ScriptedHandler()
        result = await WorkflowRunner(handler, tools, settings).run(TASK, thread_id="persistent-thread", interrupt_after="EXECUTE")
        assert result.status == WorkflowStatus.INTERRUPTED
        assert checkpoint_path(settings.checkpoint_database_url).stat().st_size > 0
        async with open_checkpointer(settings.checkpoint_database_url) as saver:
            config = {"configurable": {"thread_id": result.thread_id}}
            saved = await saver.aget_tuple(config)
            assert saved is not None
            assert saved.config["configurable"]["checkpoint_id"]
            assert saved.checkpoint["channel_values"]["run_id"] == result.run_id
            graph = build_graph(ScriptedHandler(), tools, saver)
            snapshot = await graph.aget_state(config)
            assert snapshot.next == ("TEST",)
            assert snapshot.values["phase"] == "EXECUTE"
            assert snapshot.values["status"] == "INTERRUPTED"

    asyncio.run(scenario())


@pytest.mark.parametrize("boundary", ["ANALYZE", "PLAN", "EXECUTE", "TEST", "FIX"])
def test_new_runner_resumes_without_replaying_completed_nodes(settings, boundary):
    tools, fake = registry((False, True) if boundary == "FIX" else (True,))
    first_handler = ScriptedHandler()
    expected = FIX_PATH if boundary == "FIX" else NORMAL
    first = asyncio.run(WorkflowRunner(first_handler, tools, settings).run(TASK, thread_id="resume-thread", interrupt_after=boundary))
    assert first.status == WorkflowStatus.INTERRUPTED
    cut = expected.index(boundary) + 1
    assert first_handler.calls == expected[:cut]
    # A fresh event loop, runner, registry, tool and handler have no execution memory.
    new_tools, new_fake = registry((False, True) if boundary == "FIX" else (True,))
    second_handler = ScriptedHandler()
    second = asyncio.run(WorkflowRunner(second_handler, new_tools, settings).resume(first.thread_id))
    assert second.status == WorkflowStatus.COMPLETED
    assert second_handler.calls == expected[cut:]
    assert first_handler.calls + second_handler.calls == expected
    assert (second.task_id, second.run_id, second.thread_id) == (first.task_id, first.run_id, first.thread_id)
    operations = [item["operation"] for item in fake.calls + new_fake.calls]
    assert operations.count("EXECUTE") == 1
    assert [event.sequence for event in second.trace] == list(range(1, len(second.trace) + 1))
    assert [event.event_type for event in second.trace].count(TraceEventType.WORKFLOW_STARTED) == 1


def test_identity_semantics_and_multiple_runs(settings):
    async def scenario():
        tools, _ = registry()
        first = await WorkflowRunner(ScriptedHandler(), tools, settings).run(TASK, thread_id="chosen-thread")
        second = await WorkflowRunner(ScriptedHandler(), tools, settings).run(TASK)
        assert first.task_id == second.task_id == TASK.id
        assert first.thread_id == "chosen-thread"
        assert first.run_id != first.thread_id
        assert second.run_id == second.thread_id
        assert first.run_id != second.run_id

    asyncio.run(scenario())


def test_workflow_trace_order(settings):
    tools, _ = registry()
    result = asyncio.run(WorkflowRunner(ScriptedHandler(), tools, settings).run(TASK))
    assert [event.event_type for event in result.trace] == [
        TraceEventType.WORKFLOW_STARTED,
        *([TraceEventType.NODE_STARTED, TraceEventType.NODE_COMPLETED] * 5),
        TraceEventType.WORKFLOW_COMPLETED,
    ]
    assert [event.name for event in result.trace if event.event_type == TraceEventType.NODE_STARTED] == NORMAL
    assert [event.sequence for event in result.trace] == list(range(1, 13))
    assert all(event.success for event in result.trace)


def test_interrupt_resume_trace_order(settings):
    async def scenario():
        tools, _ = registry()
        first = await WorkflowRunner(ScriptedHandler(), tools, settings).run(TASK, interrupt_after="PLAN")
        second = await WorkflowRunner(ScriptedHandler(), tools, settings).resume(first.thread_id)
        events = [event.event_type for event in second.trace]
        assert events[5:8] == [TraceEventType.WORKFLOW_INTERRUPTED, TraceEventType.WORKFLOW_RESUMED, TraceEventType.NODE_STARTED]
        assert second.trace[7].name == "EXECUTE"
        assert second.trace[:6] == first.trace

    asyncio.run(scenario())


@pytest.mark.parametrize("node", ["ANALYZE", "FIX", "REVIEW"])
def test_unrecoverable_node_error_failed_without_retry(settings, node):
    tools, _ = registry((False, True) if node == "FIX" else (True,))
    handler = ScriptedHandler(error_at=node)
    result = asyncio.run(WorkflowRunner(handler, tools, settings).run(TASK))
    assert result.status == WorkflowStatus.FAILED
    assert result.error == "node_execution_failed"
    assert handler.calls[-1] == node and handler.calls.count(node) == 1
    assert "SECRET_EXCEPTION" not in result.model_dump_json()
    assert result.trace[-2].event_type == TraceEventType.NODE_COMPLETED
    assert result.trace[-2].success is False


def test_missing_test_tool_result_is_failure(settings):
    class InvalidTestHandler(ScriptedHandler):
        async def handle(self, node, state, tools):
            if node == "TEST":
                return NodeOutcome(summary="Claimed pass without a tool")
            return await super().handle(node, state, tools)

    tools, _ = registry()
    result = asyncio.run(WorkflowRunner(InvalidTestHandler(), tools, settings).run(TASK))
    assert result.status == WorkflowStatus.FAILED
    assert result.error == "node_execution_failed"


def test_raw_tool_output_not_in_any_checkpoint_or_trace(settings):
    async def scenario():
        marker = "RAW_TOOL_SECRET_MARKER"
        tools, _ = registry(payload={"stdout": marker * 100000})
        result = await WorkflowRunner(ScriptedHandler(), tools, settings).run(TASK)
        assert marker not in result.model_dump_json()
        async with open_checkpointer(settings.checkpoint_database_url) as saver:
            async for saved in saver.alist({"configurable": {"thread_id": result.thread_id}}):
                assert marker not in json.dumps(saved.checkpoint["channel_values"])

    asyncio.run(scenario())


def test_resume_preserves_saved_iteration_limit(settings):
    tools, _ = registry((False,))
    first = asyncio.run(WorkflowRunner(ScriptedHandler(), tools, settings).run(TASK, interrupt_after="TEST"))
    assert first.iterations == 1
    changed = settings.model_copy(update={"workflow_max_iterations": 100})
    handler = ScriptedHandler()
    second = asyncio.run(WorkflowRunner(handler, tools, changed).resume(first.thread_id))
    assert second.status == WorkflowStatus.FAILED and second.iterations == 3
    assert handler.calls == ["FIX", "TEST", "FIX", "TEST"]


@pytest.mark.parametrize("failed", [False, True])
def test_resume_terminal_state_does_not_execute(settings, failed):
    tools, _ = registry((not failed,))
    first = asyncio.run(WorkflowRunner(ScriptedHandler(), tools, settings).run(TASK))
    handler = ScriptedHandler()
    second = asyncio.run(WorkflowRunner(handler, tools, settings).resume(first.thread_id))
    assert first == second
    assert handler.calls == []


def test_unknown_checkpoint_and_duplicate_thread(settings):
    async def scenario():
        tools, _ = registry()
        runner = WorkflowRunner(ScriptedHandler(), tools, settings)
        with pytest.raises(HarnessError, match="checkpoint_not_found"):
            await runner.resume("missing-thread")
        await runner.run(TASK, thread_id="existing-thread")
        with pytest.raises(HarnessError, match="thread_already_exists"):
            await runner.run(TASK, thread_id="existing-thread")

    asyncio.run(scenario())


def test_trace_snapshot_restore_preserves_existing_behavior():
    original = TraceCollector()
    original.record(0, TraceEventType.RUN_STARTED, "harness", True, "Run started")
    restored = TraceCollector.from_snapshot(original.snapshot())
    restored.record(1, TraceEventType.NODE_STARTED, "ANALYZE", True, "Node started")
    assert len(original.snapshot()) == 1
    assert [event.sequence for event in restored.snapshot()] == [1, 2]


def test_checkpoint_requires_persistent_sqlite():
    with pytest.raises(HarnessError, match="invalid_checkpoint_database_url"):
        checkpoint_path("sqlite:///:memory:")
