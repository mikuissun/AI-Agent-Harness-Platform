from contextlib import AsyncExitStack

from sqlalchemy.orm import Session

from app.adapters.qwen import QwenModelAdapter
from app.agents.handler import AgentNodeHandler
from app.agents.roles import DeveloperAgent, PlannerAgent, ReviewerAgent, TesterAgent
from app.governance.policy import RetryPolicy
from app.governance.runtime import ToolRuntime
from app.harness.errors import HarnessError
from app.harness.models import HarnessTask
from app.models.run import Run
from app.services.tasks import get_task
from app.tools.registry import workspace_registry
from app.workflow.runner import WorkflowRunner


class RunNotFound(HarnessError):
    pass


class RunConflict(HarnessError):
    pass


class RunService:
    def __init__(self, settings, engine, approvals, model_factory=QwenModelAdapter, tool_factory=workspace_registry):
        self.settings, self.engine, self.approvals = settings, engine, approvals
        self.model_factory, self.tool_factory = model_factory, tool_factory
        self._active = set()

    def get(self, run_id):
        with Session(self.engine) as session:
            row = session.get(Run, run_id)
            if row is None:
                raise RunNotFound("run_not_found")
            return {"run_id": row.run_id, "task_id": row.task_id, "thread_id": row.thread_id,
                    "status": row.status, "output": row.output, "error": row.error,
                    "pending_approval_id": row.pending_approval_id, "trace": row.trace[-100:], "usage": row.usage}

    async def run(self, task_id):
        with Session(self.engine) as session:
            task = get_task(session, task_id)
            harness_task = HarnessTask(id=task.id, title=task.title, instruction=task.instruction)
        return await self._execute(task=harness_task)

    async def resume(self, run_id):
        record = self.get(run_id)
        if record["status"] in {"COMPLETED", "FAILED"}:
            return record
        if run_id in self._active:
            raise RunConflict("run_already_active")
        with Session(self.engine) as session:
            row = session.get(Run, run_id)
            if row.workspace != str(self.settings.workspace_root.resolve()):
                raise RunConflict("run_workspace_changed")
        self._active.add(run_id)
        try:
            return await self._execute(record=record)
        finally:
            self._active.discard(run_id)

    async def _execute(self, task=None, record=None):
        previous = record["usage"] if record else {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
        remaining = self.settings.llm_max_calls - previous["calls"]
        if remaining < 1:
            raise RunConflict("qwen_call_budget_exceeded")
        runtime_settings = self.settings.model_copy(update={"llm_max_calls": remaining})
        async with AsyncExitStack() as stack:
            model = self.model_factory(runtime_settings)
            stack.push_async_callback(model.close)
            tools = await stack.enter_async_context(self.tool_factory(self.settings))
            tools.register(self.approvals.writer)
            runtime = ToolRuntime(self.approvals, RetryPolicy(self.settings.retry_max_attempts), timeout=self.settings.cli_timeout_seconds)
            handler = AgentNodeHandler(PlannerAgent(model), DeveloperAgent(model), TesterAgent(model), ReviewerAgent(model), runtime, tool_feedback=True)
            runner = WorkflowRunner(handler, tools, self.settings, node_timeout_seconds=4 * (self.settings.llm_timeout_seconds + self.settings.cli_timeout_seconds))
            result = await runner.resume(record["thread_id"]) if record else await runner.run(task)
            with Session(self.engine) as session:
                row = session.get(Run, result.run_id)
                if row is None:
                    row = Run(run_id=result.run_id, task_id=result.task_id, thread_id=result.thread_id,
                              workspace=str(self.settings.workspace_root.resolve()))
                    session.add(row)
                row.status, row.output, row.error = result.status.value, result.output, result.error
                row.pending_approval_id = result.state.get("pending_approval_id")
                row.trace = [event.model_dump(mode="json") for event in result.trace][-100:]
                row.usage = {"calls": previous["calls"] + model.calls,
                             "prompt_tokens": previous["prompt_tokens"] + model.prompt_tokens,
                             "completion_tokens": previous["completion_tokens"] + model.completion_tokens}
                session.commit()
            return self.get(result.run_id)
