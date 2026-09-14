"""Only the new role budgets and PLAN-only contract; no external services."""

import asyncio
import json
from types import SimpleNamespace as NS

import pytest

from app.adapters.qwen import QwenModelAdapter
from app.agents.handler import AgentNodeHandler
from app.agents.roles import PlannerAgent, DeveloperAgent, TesterAgent as TestingAgent, ReviewerAgent
from app.core.config import Settings
from app.harness.errors import HarnessError
from app.harness.models import ExecutionStep, FinalAction, HarnessContext, ToolCallAction, ToolDefinition, ToolExecutionResult
from app.harness.registry import ToolRegistry


class SDK:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.requests = []
        self.chat = NS(completions=self)

    async def create(self, **kwargs):
        self.requests.append(kwargs.copy())
        return NS(usage=None, choices=[NS(finish_reason="stop", message=NS(tool_calls=None, content=next(self.outputs)))])


def test_plan_repairs_empty_steps_once_with_bounded_evidence():
    sdk = SDK(['{"steps":[]}', '{"steps":["Run tests"]}'])
    adapter = QwenModelAdapter(Settings(_env_file=None), sdk)
    context = HarnessContext(task_id=1, run_id="budget", instruction="Inspect", metadata={
        "role": "PLANNER", "phase": "PLAN", "workflow_state": {
            "analysis_result": json.dumps({"workspace_files": "calculator.py", "evidence": "x" * 10000}),
            "trace": "must-not-appear", "test_result": "must-not-appear"}})
    result = asyncio.run(adapter.generate(context, [], []))
    assert json.loads(result.content)["plan"] == ["Run tests"] and adapter.calls == 2
    evidence = json.loads(sdk.requests[0]["messages"][1]["content"])
    assert set(evidence) == {"instruction", "workspace_files", "analysis_evidence", "available_tool_names"}
    assert len(evidence["analysis_evidence"]) < 1050
    assert all("tools" not in request for request in sdk.requests)


def test_plan_still_empty_fails_after_one_repair():
    adapter = QwenModelAdapter(Settings(_env_file=None), SDK(['{"steps":[]}', '{"steps":[]}']))
    with pytest.raises(HarnessError, match="qwen_invalid_plan"):
        asyncio.run(adapter.generate(HarnessContext(task_id=1, run_id="budget", instruction="Inspect", metadata={"phase": "PLAN"}), [], []))
    assert adapter.calls == 2


@pytest.mark.parametrize("phase,history_count", [("ANALYZE", 2), ("EXECUTE", 4), ("TEST", 2), ("REVIEW", 0)])
def test_role_budget_removes_tools_from_final_request(phase, history_count):
    sdk = SDK(['{"type":"FINAL","content":{"success":true,"output":"Summary"}}'])
    tool = ToolDefinition(name="git_status", description="Status", input_schema={"type": "object"})
    history = [ExecutionStep(action=ToolCallAction(tool_name="git_status", arguments={}), tool_result=ToolExecutionResult(success=True, data="status")) for _ in range(history_count)]
    result = asyncio.run(QwenModelAdapter(Settings(_env_file=None), sdk).generate(
        HarnessContext(task_id=1, run_id="budget", instruction="Inspect", metadata={"phase": phase}), [tool], history))
    assert isinstance(result, FinalAction)
    assert len(sdk.requests) == 1 and "tools" not in sdk.requests[0]
    assert sdk.requests[0]["response_format"] == {"type": "json_object"}


def test_handler_will_not_execute_call_after_analyze_budget():
    class Model:
        calls = 0

        async def generate(self, context, tools, history):
            self.calls += 1
            return ToolCallAction(tool_name="git_status", arguments={"index": self.calls})

    class Runtime:
        calls = 0

        async def call(self, *args):
            self.calls += 1
            return NS(approval_id=None, result=ToolExecutionResult(success=True, data="status"))

    model, runtime = Model(), Runtime()
    handler = AgentNodeHandler(PlannerAgent(model), DeveloperAgent(model), TestingAgent(model), ReviewerAgent(model), runtime, tool_feedback=True)
    state = {"task_id": 1, "run_id": "budget", "instruction": "Inspect", "phase": "ANALYZE", "iteration": 0, "max_iterations": 3, "trace": []}
    result = asyncio.run(handler.handle("ANALYZE", state, ToolRegistry()))
    assert result.fatal_error == "role_tool_budget_exceeded"
    assert model.calls == 3 and runtime.calls == 2
