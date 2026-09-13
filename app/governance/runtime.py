import asyncio
from dataclasses import dataclass

from app.governance.approval import ApprovalService
from app.governance.policy import PermissionPolicy, RetryPolicy
from app.harness.errors import HarnessError
from app.harness.models import ToolCallAction, ToolExecutionResult, TraceEventType
from app.harness.registry import ToolRegistry
from app.harness.trace import TraceCollector


@dataclass
class GovernedResult:
    result: ToolExecutionResult | None = None
    approval_id: str | None = None
    attempts: int = 0


class ToolRuntime:
    def __init__(self, approvals: ApprovalService, retry: RetryPolicy, sleeper=asyncio.sleep, timeout: float = 20):
        self.approvals, self.retry, self.sleeper, self.timeout = approvals, retry, sleeper, timeout

    async def call(self, tools: ToolRegistry, action: ToolCallAction, state: dict, role: str, trace: TraceCollector) -> GovernedResult:
        iteration = state["iteration"]
        try:
            tool = tools.get(action.tool_name)
            if not tools.validate_arguments(action.tool_name, action.arguments):
                return GovernedResult(ToolExecutionResult(success=False, error="invalid_arguments"))
        except HarnessError:
            return GovernedResult(ToolExecutionResult(success=False, error="unknown_tool"))
        risk = tool.definition.risk_level
        if not PermissionPolicy.allows(role, risk):
            trace.record(iteration, TraceEventType.PERMISSION_DENIED, action.tool_name, False, "Permission denied")
            return GovernedResult(ToolExecutionResult(success=False, error="permission_denied"))
        if PermissionPolicy.requires_approval(risk):
            try:
                identifier = self.approvals.request(state["task_id"], state["run_id"], role, action.tool_name, action.arguments)
            except HarnessError as exc:
                trace.record(iteration, TraceEventType.PERMISSION_DENIED, action.tool_name, False, "Write request rejected")
                return GovernedResult(ToolExecutionResult(success=False, error=str(exc)))
            trace.record(iteration, TraceEventType.APPROVAL_REQUIRED, action.tool_name, True, "Human approval required")
            return GovernedResult(approval_id=identifier)
        for attempt in range(1, self.retry.max_attempts + 1):
            if attempt > 1:
                trace.record(iteration, TraceEventType.RETRY_STARTED, action.tool_name, True, f"Attempt {attempt}")
                if self.retry.backoff_seconds:
                    await self.sleeper(self.retry.backoff_seconds)
            trace.record(iteration, TraceEventType.TOOL_CALLED, action.tool_name, True, f"Attempt {attempt}")
            try:
                result = await asyncio.wait_for(tool.execute(action.model_copy(deep=True).arguments), self.timeout)
                result = ToolExecutionResult.model_validate(result.model_dump(warnings="error"))
            except TimeoutError:
                result = ToolExecutionResult(success=False, error="tool_timeout")
            except Exception:
                result = ToolExecutionResult(success=False, error="tool_execution_failed")
            trace.record(iteration, TraceEventType.TOOL_COMPLETED, action.tool_name, result.success, "Tool completed" if result.success else "Tool failed")
            if result.success or not self.retry.retryable(result.error):
                return GovernedResult(result, attempts=attempt)
        trace.record(iteration, TraceEventType.RETRY_EXHAUSTED, action.tool_name, False, "Attempts exhausted")
        return GovernedResult(ToolExecutionResult(success=False, error="retry_exhausted"), attempts=self.retry.max_attempts)
