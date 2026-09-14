import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace as NS

import httpx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from openai import APIConnectionError
from pydantic import SecretStr
import pytest

from app.adapters.qwen import QwenModelAdapter
from app.core.config import Settings
from app.harness.errors import HarnessError
from app.harness.models import ExecutionStep, FinalAction, HarnessContext, ToolCallAction, ToolDefinition, ToolExecutionResult, ToolRiskLevel
from app.harness.registry import ToolRegistry
from app.main import create_app


def settings(tmp_path):
    return Settings(_env_file=None, workspace_root=tmp_path,
        database_url=f"sqlite:///{(tmp_path / 'tasks.db').as_posix()}",
        checkpoint_database_url=f"sqlite:///{(tmp_path / 'checkpoints.db').as_posix()}",
        dashscope_api_key=SecretStr("unit-test-placeholder"), approval_encryption_key=SecretStr(Fernet.generate_key().decode()))


class SDKMock:
    def __init__(self, content=None, calls=None, error=None):
        self.chat = NS(completions=self)
        self.response = NS(choices=[NS(finish_reason="tool_calls" if calls else "stop", message=NS(content=content, tool_calls=calls))],
                           usage=NS(prompt_tokens=10, completion_tokens=5))
        self.requests = []
        self.error = error

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        if self.error:
            raise self.error
        return self.response

    async def close(self):
        pass


def context():
    return HarnessContext(task_id=1, run_id="r", instruction="Inspect", metadata={"role": "DEVELOPER", "phase": "EXECUTE"})


def definition():
    return ToolDefinition(name="mcp.workspace_read_text", description="Read", input_schema={"type": "object", "properties": {"path": {"type": "string"}}})


def test_qwen_final_messages_tools_usage(tmp_path):
    sdk = SDKMock(content=json.dumps({"type": "FINAL", "content": {"success": True, "output": "planned", "plan": ["inspect"]}}))
    adapter = QwenModelAdapter(settings(tmp_path), sdk)
    result = asyncio.run(adapter.generate(context(), [definition()], []))
    assert isinstance(result, FinalAction)
    assert json.loads(result.content)["plan"] == ["inspect"]
    request = sdk.requests[0]
    assert [message["role"] for message in request["messages"]] == ["system", "user"]
    assert request["tools"][0]["function"]["name"] == "mcp__workspace_read_text"
    assert "response_format" not in request
    assert adapter.calls == 1 and adapter.prompt_tokens == 10 and adapter.completion_tokens == 5
    assert "unit-test-placeholder" not in json.dumps(request)


def test_native_tool_call_maps_to_existing_action(tmp_path):
    sdk = SDKMock(calls=[NS(function=NS(name="mcp__workspace_read_text", arguments='{"path":"calculator.py"}'))])
    current = context().model_copy(update={"metadata": {"role": "DEVELOPER", "phase": "EXECUTE"}})
    result = asyncio.run(QwenModelAdapter(settings(tmp_path), sdk).generate(current, [definition()], []))
    assert result == ToolCallAction(tool_name="mcp.workspace_read_text", arguments={"path": "calculator.py"})
    assert "response_format" not in sdk.requests[0]


@pytest.mark.parametrize("content", ["free text", '{"type":"OTHER"}', '{"type":"FINAL","content":{"success":"yes","output":"bad"}}'])
def test_invalid_qwen_output_is_safe_error(tmp_path, content):
    with pytest.raises(HarnessError, match="qwen_invalid_output"):
        asyncio.run(QwenModelAdapter(settings(tmp_path), SDKMock(content=content)).generate(context(), [], []))


def test_qwen_api_error_has_no_secret(tmp_path):
    sdk = SDKMock(error=APIConnectionError(message="unit-secret-do-not-expose", request=httpx.Request("POST", "https://example.invalid")))
    with pytest.raises(HarnessError) as failure:
        asyncio.run(QwenModelAdapter(settings(tmp_path), sdk).generate(context(), [], []))
    assert str(failure.value) == "qwen_api_error"


def test_tool_history_is_bounded_and_protocol_linked(tmp_path):
    sdk = SDKMock(content='{"type":"FINAL","content":"done"}')
    history = [ExecutionStep(action=ToolCallAction(tool_name=definition().name, arguments={"path": "a.txt"}), tool_result=ToolExecutionResult(success=True, data="x" * 100000))]
    asyncio.run(QwenModelAdapter(settings(tmp_path), sdk).generate(context(), [definition()], history))
    messages = sdk.requests[0]["messages"]
    assert messages[-1]["role"] == "tool"
    assert messages[-1]["tool_call_id"] == messages[-2]["tool_calls"][0]["id"]
    assert len(messages[-1]["content"]) < 3100


def test_model_call_limit_no_retry(tmp_path):
    configured = settings(tmp_path).model_copy(update={"llm_max_calls": 1})
    sdk = SDKMock(content='{"type":"FINAL","content":"done"}')
    adapter = QwenModelAdapter(configured, sdk)
    asyncio.run(adapter.generate(context(), [], []))
    with pytest.raises(HarnessError, match="qwen_call_budget_exceeded"):
        asyncio.run(adapter.generate(context(), [], []))
    assert len(sdk.requests) == 1


def test_tester_summarizes_after_actual_tool_result(tmp_path):
    sdk = SDKMock(content='{"type":"FINAL","content":{"success":true,"output":"1 passed"}}')
    current = context().model_copy(update={"metadata": {"role": "TESTER", "phase": "TEST"}})
    tool = definition().model_copy(update={"name": "run_pytest"})
    history = [ExecutionStep(action=ToolCallAction(tool_name="run_pytest", arguments={"paths": ["tests/"]}), tool_result=ToolExecutionResult(success=True, data="1 passed"))]
    asyncio.run(QwenModelAdapter(settings(tmp_path), sdk).generate(current, [tool], history))
    assert "tools" not in sdk.requests[0]
    assert sdk.requests[0]["response_format"] == {"type": "json_object"}
    assert "1 passed" in sdk.requests[0]["messages"][-1]["content"]


class ScriptedModel:
    def __init__(self, settings, write=False):
        self.calls = self.prompt_tokens = self.completion_tokens = 0
        self.write = write
        self.phases = []

    async def generate(self, context, tools, history):
        self.calls += 1
        phase = context.metadata["phase"]
        self.phases.append(phase)
        if phase == "EXECUTE" and self.write:
            return ToolCallAction(tool_name="write_text_file", arguments={"path": "result.txt", "content": "demo content"})
        if phase == "TEST" and not history:
            return ToolCallAction(tool_name="run_pytest", arguments={"paths": ["tests/"]})
        return FinalAction(content=json.dumps({"success": True, "output": "Verified", "plan": ["inspect"] if phase == "PLAN" else [], "decision": "PASS" if phase == "REVIEW" else None}))

    async def close(self):
        pass


@asynccontextmanager
async def fake_registry(settings):
    class FakeTestTool:
        definition = ToolDefinition(name="run_pytest", description="Fixture", risk_level=ToolRiskLevel.SAFE_EXECUTION,
                                    input_schema={"type": "object", "properties": {"paths": {"type": "array"}}})

        async def execute(self, arguments):
            return ToolExecutionResult(success=True, data={"stdout": "1 passed"})

    tools = ToolRegistry()
    tools.register(FakeTestTool())
    yield tools


def test_run_api_and_persisted_status(tmp_path):
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        app.state.runs.model_factory = ScriptedModel
        app.state.runs.tool_factory = fake_registry
        task = client.post("/api/tasks", json={"title": "demo", "instruction": "verify"}).json()
        response = client.post(f"/api/tasks/{task['id']}/run")
        assert response.status_code == 200
        run = response.json()
        assert run["status"] == "COMPLETED" and run["usage"]["calls"] == 6
        assert client.get(f"/api/runs/{run['run_id']}").json() == run
        assert "unit-test-placeholder" not in response.text
        assert client.get("/api/runs/missing").status_code == 404
        assert client.post("/api/tasks/999/run").status_code == 404
    with TestClient(create_app(settings(tmp_path))) as reopened:
        assert reopened.get(f"/api/runs/{run['run_id']}").json()["status"] == "COMPLETED"


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_run_api_approval_governance_and_resume(tmp_path, decision):
    configured = settings(tmp_path)
    app = create_app(configured)
    models = []

    def factory(config):
        model = ScriptedModel(config, write=True)
        models.append(model)
        return model

    with TestClient(app) as client:
        app.state.runs.model_factory = factory
        app.state.runs.tool_factory = fake_registry
        task = client.post("/api/tasks", json={"title": "write", "instruction": "write approved demo"}).json()
        run = client.post(f"/api/tasks/{task['id']}/run").json()
        assert run["status"] == "INTERRUPTED" and not (tmp_path / "result.txt").exists()
        assert "demo content" not in json.dumps(run)
        assert client.post(f"/api/approvals/{run['pending_approval_id']}/{decision}").status_code == 200
        resumed = client.post(f"/api/runs/{run['run_id']}/resume")
        assert resumed.status_code == 200
        result = resumed.json()
        assert result["status"] == ("COMPLETED" if decision == "approve" else "FAILED")
        assert (tmp_path / "result.txt").exists() == (decision == "approve")
        assert "EXECUTE" not in models[-1].phases
        assert result["usage"]["calls"] >= run["usage"]["calls"]
