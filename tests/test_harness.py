import asyncio

import pytest

from app.harness.errors import DuplicateToolError, HarnessError
from app.harness.models import (
    FinalAction, HarnessTask, TaskStatus, ToolCallAction, ToolDefinition,
    ToolExecutionResult, TraceEventType,
)
from app.harness.registry import ToolRegistry
from app.harness.runner import HarnessRunner


TASK = HarnessTask(id=1, title="Test task", instruction="Complete the test task")


class ScriptedModel:
    def __init__(self, *actions):
        self.actions = list(actions)
        self.calls = []

    async def generate(self, context, tools, history):
        self.calls.append((context, tools, history))
        action = self.actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


class FakeTool:
    def __init__(self, result=None, exception=None):
        self.calls = []
        self.result = result if result is not None else ToolExecutionResult(success=True, data={"value": 42})
        self.exception = exception
        self.definition = ToolDefinition(
            name="lookup", description="Fake lookup",
            input_schema={
                "type": "object", "properties": {"query": {"type": "string"}},
                "required": ["query"], "additionalProperties": False,
            },
        )

    async def execute(self, arguments):
        self.calls.append(arguments)
        if self.exception:
            raise self.exception
        return self.result


def tool_call(query="test"):
    return ToolCallAction(tool_name="lookup", arguments={"query": query})


def run(model, tool=None, **kwargs):
    registry = ToolRegistry()
    if tool is not None:
        registry.register(tool)
    return asyncio.run(HarnessRunner(model, registry, **kwargs).run(TASK))


def test_direct_final():
    model = ScriptedModel(FinalAction(content="Done"))
    result = run(model)
    assert result.status == TaskStatus.COMPLETED
    assert result.output == "Done"
    assert result.error is None
    assert result.iterations == 1
    context, tools, history = model.calls[0]
    assert context.task_id == TASK.id
    assert context.run_id == result.run_id
    assert context.instruction == TASK.instruction
    assert context.iteration == 1
    assert context.max_iterations == 8
    assert context.metadata == {}
    assert tools == history == []


def test_single_tool_then_final():
    tool = FakeTool()
    model = ScriptedModel(tool_call(), FinalAction(content="Done"))
    result = run(model, tool)
    assert result.status == TaskStatus.COMPLETED
    assert result.iterations == 2
    assert tool.calls == [{"query": "test"}]
    assert model.calls[0][1] == [tool.definition]
    history = model.calls[1][2]
    assert len(history) == 1
    assert history[0].action == tool_call()
    assert history[0].tool_result.data == {"value": 42}


def test_two_tools_then_final():
    tool = FakeTool()
    model = ScriptedModel(tool_call("one"), tool_call("two"), FinalAction(content="Done"))
    result = run(model, tool)
    assert result.status == TaskStatus.COMPLETED
    assert result.iterations == 3
    assert tool.calls == [{"query": "one"}, {"query": "two"}]
    assert [len(call[2]) for call in model.calls] == [0, 1, 2]
    assert [call[0].iteration for call in model.calls] == [1, 2, 3]


def test_unknown_tool():
    result = run(ScriptedModel(tool_call()))
    assert result.status == TaskStatus.FAILED
    assert result.error == "unknown_tool"
    assert result.iterations == 1
    assert result.trace[-1].event_type == TraceEventType.RUN_FAILED


@pytest.mark.parametrize("raises", [False, True])
def test_tool_failure_model_continues(raises):
    tool = FakeTool(
        result=ToolExecutionResult(success=False, error="Unavailable"),
        exception=RuntimeError("secret exception text") if raises else None,
    )
    model = ScriptedModel(tool_call(), FinalAction(content="Handled failure"))
    result = run(model, tool)
    assert result.status == TaskStatus.COMPLETED
    assert result.output == "Handled failure"
    failure = model.calls[1][2][0].tool_result
    assert failure.success is False
    assert failure.error == ("tool_execution_failed" if raises else "Unavailable")
    assert result.trace[3].success is False
    assert "secret exception text" not in result.model_dump_json()


def test_duplicate_tool():
    registry = ToolRegistry()
    original = FakeTool()
    registry.register(original)
    with pytest.raises(DuplicateToolError, match="duplicate_tool_name"):
        registry.register(FakeTool())
    assert registry.get("lookup") is original
    assert len(registry.definitions()) == 1


def test_max_iterations():
    tool = FakeTool()
    model = ScriptedModel(*(tool_call() for _ in range(9)))
    result = run(model, tool)
    assert result.status == TaskStatus.FAILED
    assert result.error == "max_iterations_exceeded"
    assert result.iterations == len(model.calls) == len(tool.calls) == 8
    assert len(model.actions) == 1


def test_trace_order_and_payload_exclusion():
    secret = "SECRET_MARKER"
    tool = FakeTool(result=ToolExecutionResult(success=True, data=secret * 10000))
    result = run(ScriptedModel(tool_call(secret), FinalAction(content=secret)), tool)
    assert [event.event_type for event in result.trace] == [
        TraceEventType.RUN_STARTED, TraceEventType.MODEL_CALLED,
        TraceEventType.TOOL_CALLED, TraceEventType.TOOL_COMPLETED,
        TraceEventType.MODEL_CALLED, TraceEventType.RUN_COMPLETED,
    ]
    assert [event.sequence for event in result.trace] == list(range(1, 7))
    assert [event.iteration for event in result.trace] == [0, 1, 1, 1, 2, 2]
    assert result.trace[2].name == "lookup"
    assert all(event.success for event in result.trace)
    assert all(secret not in event.model_dump_json() for event in result.trace)
    assert all(len(event.summary) <= 200 for event in result.trace)


@pytest.mark.parametrize("action", [
    "FINAL: done",
    {"type": "FINAL", "content": "done"},
    ToolCallAction.model_construct(tool_name="lookup", arguments="invalid"),
])
def test_invalid_action(action):
    tool = FakeTool()
    result = run(ScriptedModel(action), tool)
    assert result.status == TaskStatus.FAILED
    assert result.error == "invalid_action"
    assert tool.calls == []


def test_invalid_arguments_never_execute():
    tool = FakeTool()
    result = run(ScriptedModel(ToolCallAction(tool_name="lookup", arguments={})), tool)
    assert result.error == "invalid_tool_arguments"
    assert tool.calls == []


def test_model_exception_is_safe_failure():
    result = run(ScriptedModel(RuntimeError("secret exception text")))
    assert result.status == TaskStatus.FAILED
    assert result.error == "model_call_failed"
    assert "secret exception text" not in result.model_dump_json()


def test_tool_timeout_model_continues():
    class WaitingTool(FakeTool):
        async def execute(self, arguments):
            await asyncio.Event().wait()

    model = ScriptedModel(tool_call(), FinalAction(content="Timed out safely"))
    result = run(model, WaitingTool(), call_timeout_seconds=0.01)
    assert result.status == TaskStatus.COMPLETED
    assert model.calls[1][2][0].tool_result.error == "tool_execution_failed"


def test_model_timeout():
    class WaitingModel:
        async def generate(self, context, tools, history):
            await asyncio.Event().wait()

    result = run(WaitingModel(), call_timeout_seconds=0.01)
    assert result.status == TaskStatus.FAILED
    assert result.error == "model_call_failed"


def test_invalid_iteration_limit():
    with pytest.raises(HarnessError, match="invalid_max_iterations"):
        HarnessRunner(ScriptedModel(), max_iterations=0)


def test_schema_references_rejected():
    tool = FakeTool()
    tool.definition.input_schema = {"$ref": "https://example.com/schema"}
    with pytest.raises(HarnessError, match="tool_schema_references_not_supported"):
        ToolRegistry().register(tool)


def test_runs_are_isolated():
    model = ScriptedModel(FinalAction(content="one"), FinalAction(content="two"))
    runner = HarnessRunner(model)
    first = asyncio.run(runner.run(TASK, metadata={"source": "test"}))
    second = asyncio.run(runner.run(TASK))
    assert first.run_id != second.run_id
    assert first.iterations == second.iterations == 1
    assert model.calls[0][0].metadata == {"source": "test"}
    assert model.calls[1][0].metadata == {}
    assert model.calls[1][2] == []
    assert second.trace[0].sequence == 1
