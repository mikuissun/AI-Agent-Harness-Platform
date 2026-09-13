import asyncio
from uuid import uuid4

from pydantic import JsonValue, TypeAdapter

from app.harness.errors import HarnessError, UnknownToolError
from app.harness.interfaces import ModelAdapter
from app.harness.models import (
    ExecutionStep, FinalAction, HarnessAction, HarnessContext, HarnessRunResult,
    HarnessTask, TaskStatus, ToolCallAction, ToolExecutionResult, TraceEventType,
)
from app.harness.registry import ToolRegistry
from app.harness.trace import TraceCollector


class HarnessRunner:
    def __init__(
        self, model: ModelAdapter, tools: ToolRegistry | None = None,
        max_iterations: int = 8, call_timeout_seconds: float = 30.0,
    ) -> None:
        if type(max_iterations) is not int or max_iterations < 1:
            raise HarnessError("invalid_max_iterations")
        if not 0 < call_timeout_seconds < float("inf"):
            raise HarnessError("invalid_call_timeout")
        self.model = model
        self.tools = tools if tools is not None else ToolRegistry()
        self.max_iterations = max_iterations
        self.call_timeout_seconds = call_timeout_seconds

    async def run(
        self, task: HarnessTask, metadata: dict[str, JsonValue] | None = None,
    ) -> HarnessRunResult:
        context = HarnessContext(
            task_id=task.id, run_id=str(uuid4()), instruction=task.instruction,
            max_iterations=self.max_iterations, metadata=metadata or {},
        )
        trace = TraceCollector()
        history: list[ExecutionStep] = []
        trace.record(0, TraceEventType.RUN_STARTED, "harness", True, "Run started")

        def finish(error: str | None = None, output: str | None = None) -> HarnessRunResult:
            trace.record(
                context.iteration,
                TraceEventType.RUN_FAILED if error else TraceEventType.RUN_COMPLETED,
                "harness", error is None, error or "Run completed",
            )
            return HarnessRunResult(
                task_id=task.id, run_id=context.run_id,
                status=TaskStatus.FAILED if error else TaskStatus.COMPLETED,
                output=output, error=error, iterations=context.iteration, trace=trace.snapshot(),
            )

        for iteration in range(1, self.max_iterations + 1):
            context.iteration = iteration
            try:
                action = await asyncio.wait_for(
                    self.model.generate(
                        context.model_copy(deep=True), self.tools.definitions(),
                        [step.model_copy(deep=True) for step in history],
                    ), timeout=self.call_timeout_seconds,
                )
            except Exception:
                trace.record(iteration, TraceEventType.MODEL_CALLED, "model", False, "Model call failed")
                return finish("model_call_failed")
            try:
                if not isinstance(action, (FinalAction, ToolCallAction)):
                    raise ValueError("Expected a structured action")
                action = TypeAdapter(HarnessAction).validate_python(action.model_dump(warnings="error"))
            except Exception:
                trace.record(iteration, TraceEventType.MODEL_CALLED, "model", False, "Invalid action")
                return finish("invalid_action")
            trace.record(iteration, TraceEventType.MODEL_CALLED, "model", True, "Structured action received")
            if isinstance(action, FinalAction):
                return finish(output=action.content)
            try:
                tool = self.tools.get(action.tool_name)
            except UnknownToolError:
                return finish("unknown_tool")
            if not self.tools.validate_arguments(action.tool_name, action.arguments):
                return finish("invalid_tool_arguments")
            trace.record(iteration, TraceEventType.TOOL_CALLED, action.tool_name, True, "Tool call started")
            try:
                result = await asyncio.wait_for(
                    tool.execute(action.model_copy(deep=True).arguments),
                    timeout=self.call_timeout_seconds,
                )
                if not isinstance(result, ToolExecutionResult):
                    raise ValueError("Expected a structured tool result")
                result = ToolExecutionResult.model_validate(result.model_dump(warnings="error"))
            except Exception:
                result = ToolExecutionResult(success=False, error="tool_execution_failed")
            trace.record(
                iteration, TraceEventType.TOOL_COMPLETED, action.tool_name, result.success,
                "Tool completed" if result.success else "Tool failed",
            )
            history.append(ExecutionStep(action=action, tool_result=result))
        return finish("max_iterations_exceeded")
