import asyncio
import json

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.handler import AgentNodeHandler
from app.agents.roles import DeveloperAgent, PlannerAgent, ReviewerAgent, TesterAgent as TestingAgent
from app.core.config import Settings
from app.db.base import Base
from app.db.session import create_db_engine
from app.governance.approval import ApprovalConflict, ApprovalService
from app.governance.policy import RetryPolicy
from app.governance.runtime import ToolRuntime
from app.harness.models import FinalAction, HarnessTask, ToolCallAction, ToolDefinition, ToolExecutionResult, ToolRiskLevel, TraceEventType
from app.harness.registry import ToolRegistry
from app.harness.trace import TraceCollector
from app.main import create_app
from app.models.approval import PendingApproval
from app.tools.workspace import WorkspacePolicy
from app.tools.write import WriteTextFile
from app.workflow.runner import WorkflowRunner


TASK = HarnessTask(id=7, title="Update text", instruction="Update and verify workspace")
SECRET = "file-body-SECRET-do-not-log"


class FakeTool:
    def __init__(self, risk=ToolRiskLevel.READ_ONLY, errors=()):
        self.definition = ToolDefinition(name="demo", description="Test tool", input_schema={"type": "object", "additionalProperties": False}, risk_level=risk)
        self.errors = list(errors)
        self.calls = 0

    async def execute(self, arguments):
        self.calls += 1
        error = self.errors.pop(0) if self.errors else None
        return ToolExecutionResult(success=error is None, error=error)


class ScriptedModel:
    def __init__(self, write=False, needs_fix=False, invalid=False):
        self.calls = []
        self.write, self.needs_fix, self.invalid = write, needs_fix, invalid

    async def generate(self, context, tools, history):
        role, phase = context.metadata["role"], context.metadata["phase"]
        self.calls.append((role, phase))
        assert context.metadata["attempt"] == 1
        if self.invalid:
            return FinalAction(content="not a structured agent result")
        if phase in {"EXECUTE", "FIX", "TEST"}:
            if self.write and phase == "EXECUTE":
                return ToolCallAction(tool_name="write_text_file", arguments={"path": "result.txt", "content": SECRET})
            return ToolCallAction(tool_name="demo", arguments={})
        data = {"success": True, "output": f"{phase} done"}
        if phase == "PLAN":
            data["plan"] = ["Update target", "Verify tests"]
        if phase == "REVIEW":
            data["decision"] = "NEEDS_FIX" if self.needs_fix and self.calls.count((role, phase)) == 1 else "PASS"
        return FinalAction(content=json.dumps(data))


@pytest.fixture
def setup(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    settings = Settings(_env_file=None, workspace_root=workspace,
                        database_url=f"sqlite:///{(tmp_path / 'tasks.db').as_posix()}",
                        checkpoint_database_url=f"sqlite:///{(tmp_path / 'checkpoints.db').as_posix()}",
                        approval_encryption_key=SecretStr(Fernet.generate_key().decode("ascii")))
    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(engine)
    writer = WriteTextFile(WorkspacePolicy(workspace), settings.write_max_chars)
    service = ApprovalService(engine, writer, settings.approval_encryption_key)
    tools = ToolRegistry()
    tools.register(writer)
    fake = FakeTool()
    tools.register(fake)
    yield settings, tools, service, fake
    engine.dispose()


def handler(model, service, retry=None, sleeper=None):
    kwargs = {"sleeper": sleeper} if sleeper else {}
    runtime = ToolRuntime(service, retry or RetryPolicy(), **kwargs)
    return AgentNodeHandler(PlannerAgent(model), DeveloperAgent(model), TestingAgent(model), ReviewerAgent(model), runtime)


def test_reviewer_must_provide_explicit_decision(setup):
    class InvalidReviewer(ScriptedModel):
        async def generate(self, context, tools, history):
            if context.metadata["phase"] == "REVIEW":
                return ToolCallAction(tool_name="demo")
            return await super().generate(context, tools, history)

    settings, tools, service, fake = setup
    result = asyncio.run(WorkflowRunner(handler(InvalidReviewer(), service), tools, settings).run(TASK))
    assert result.status == "FAILED" and result.error == "invalid_agent_result"
    assert fake.calls == 2


def test_test_failure_uses_fix_not_retry(setup):
    settings, tools, service, fake = setup
    fake.errors = [None, "test_failed", None, None]
    model = ScriptedModel()
    result = asyncio.run(WorkflowRunner(handler(model, service), tools, settings).run(TASK))
    assert result.status == "COMPLETED"
    assert [node for _, node in model.calls] == ["ANALYZE", "PLAN", "EXECUTE", "TEST", "FIX", "TEST", "REVIEW"]
    assert fake.calls == 4
    assert not any(event.event_type == TraceEventType.RETRY_STARTED for event in result.trace)


def test_roles_follow_workflow_chain(setup):
    settings, tools, service, fake = setup
    model = ScriptedModel()
    result = asyncio.run(WorkflowRunner(handler(model, service), tools, settings).run(TASK))
    assert result.status == "COMPLETED"
    assert model.calls == [("PLANNER", "ANALYZE"), ("PLANNER", "PLAN"), ("DEVELOPER", "EXECUTE"), ("TESTER", "TEST"), ("REVIEWER", "REVIEW")]
    assert fake.calls == 2
    starts = [event.name for event in result.trace if event.event_type == TraceEventType.AGENT_STARTED]
    assert starts == [role for role, _ in model.calls]


def test_reviewer_needs_fix_routes_to_developer(setup):
    settings, tools, service, _ = setup
    model = ScriptedModel(needs_fix=True)
    result = asyncio.run(WorkflowRunner(handler(model, service), tools, settings).run(TASK))
    assert result.status == "COMPLETED"
    assert [node for _, node in model.calls] == ["ANALYZE", "PLAN", "EXECUTE", "TEST", "REVIEW", "FIX", "TEST", "REVIEW"]
    assert result.iterations == 2


def test_invalid_agent_result_fails_once(setup):
    settings, tools, service, _ = setup
    model = ScriptedModel(invalid=True)
    result = asyncio.run(WorkflowRunner(handler(model, service), tools, settings).run(TASK))
    assert result.status == "FAILED" and result.error == "invalid_agent_result"
    assert len(model.calls) == 1


async def call_runtime(setup, fake, retry, sleeper=None):
    _, _, service, _ = setup
    tools = ToolRegistry()
    tools.register(fake)
    trace = TraceCollector()
    runtime = ToolRuntime(service, retry, **({"sleeper": sleeper} if sleeper else {}))
    result = await runtime.call(tools, ToolCallAction(tool_name="demo"), {"task_id": 7, "run_id": "retry-run", "iteration": 1}, "DEVELOPER", trace)
    return result, trace.snapshot()


def test_retry_success_and_injected_backoff(setup):
    fake = FakeTool(errors=["tool_timeout"])
    sleeps = []

    async def no_sleep(seconds):
        sleeps.append(seconds)

    result, events = asyncio.run(call_runtime(setup, fake, RetryPolicy(2, 0.5), no_sleep))
    assert result.result.success and result.attempts == fake.calls == 2
    assert sleeps == [0.5]
    assert [event.event_type for event in events] == [TraceEventType.TOOL_CALLED, TraceEventType.TOOL_COMPLETED, TraceEventType.RETRY_STARTED, TraceEventType.TOOL_CALLED, TraceEventType.TOOL_COMPLETED]


@pytest.mark.parametrize("error", ["invalid_arguments", "workspace_escape", "permission_denied", "unknown_tool", "invalid_action"])
def test_nonretryable_errors(setup, error):
    fake = FakeTool(errors=[error])
    result, events = asyncio.run(call_runtime(setup, fake, RetryPolicy()))
    assert not result.result.success and fake.calls == 1
    assert not any(event.event_type == TraceEventType.RETRY_STARTED for event in events)


def test_retry_exhaustion_fails_workflow(setup):
    settings, _, service, _ = setup
    tools = ToolRegistry()
    fake = FakeTool(errors=["mcp_call_tool_failed"] * 3)
    tools.register(fake)
    result = asyncio.run(WorkflowRunner(handler(ScriptedModel(), service), tools, settings).run(TASK))
    assert result.status == "FAILED" and fake.calls == 2
    assert any(event.event_type == TraceEventType.RETRY_EXHAUSTED for event in result.trace)


@pytest.mark.parametrize("risk", [ToolRiskLevel.READ_ONLY, ToolRiskLevel.SAFE_EXECUTION])
def test_low_risk_tools_execute_without_approval(setup, risk):
    fake = FakeTool(risk=risk)
    result, _ = asyncio.run(call_runtime(setup, fake, RetryPolicy()))
    assert result.result.success and result.approval_id is None
    with Session(setup[2].engine) as session:
        assert session.scalars(select(PendingApproval)).all() == []


def test_privileged_tool_denied(setup):
    fake = FakeTool(risk=ToolRiskLevel.PRIVILEGED)
    result, events = asyncio.run(call_runtime(setup, fake, RetryPolicy()))
    assert result.result.error == "permission_denied" and fake.calls == 0
    assert events[-1].event_type == TraceEventType.PERMISSION_DENIED


def test_registry_cannot_bypass_write_approval(setup):
    settings, tools, _, _ = setup
    result = asyncio.run(tools.get("write_text_file").execute({"path": "result.txt", "content": SECRET}))
    assert result.error == "permission_denied"
    assert not (settings.workspace_root / "result.txt").exists()


def request_write(setup):
    return setup[2].request(TASK.id, "approval-run", "DEVELOPER", "write_text_file", {"path": "result.txt", "content": SECRET})


def test_approval_key_cannot_be_read_through_workspace_tools(setup):
    from app.harness.errors import HarnessError
    settings, _, _, _ = setup
    path = settings.workspace_root / "local.approval-key"
    path.write_text("test-key-placeholder")
    with pytest.raises(HarnessError, match="sensitive_path_denied"):
        WorkspacePolicy(settings.workspace_root).resolve(path.name)


def test_pending_approval_is_encrypted_and_does_not_write(setup):
    settings, _, service, _ = setup
    identifier = request_write(setup)
    record = service.get(identifier)
    assert record["status"] == "PENDING" and record["requested_by"] == "DEVELOPER"
    assert not (settings.workspace_root / "result.txt").exists()
    assert SECRET not in str(record)
    with Session(service.engine) as session:
        row = session.get(PendingApproval, identifier)
        assert SECRET.encode() not in row.encrypted_payload
    assert record["arguments_summary"]["characters"] == len(SECRET)


def test_approval_apis_execute_once_and_audit(setup):
    settings, _, service, _ = setup
    identifier = request_write(setup)
    with TestClient(create_app(settings)) as client:
        assert client.get(f"/api/approvals/{identifier}").json()["status"] == "PENDING"
        response = client.post(f"/api/approvals/{identifier}/approve")
        assert response.status_code == 200
        record = response.json()
        assert record["status"] == "EXECUTED" and record["decided_by"] == "human"
        assert record["decision"] == "APPROVE" and record["decided_at"] and record["finished_at"]
        assert SECRET not in response.text
        assert (settings.workspace_root / "result.txt").read_text() == SECRET
        assert client.post(f"/api/approvals/{identifier}/approve").status_code == 409
        assert client.post(f"/api/approvals/{identifier}/reject").status_code == 409
        assert client.get("/api/approvals/missing").status_code == 404
    assert service.get(identifier)["status"] == "EXECUTED"


def test_reject_api_does_not_write_and_conflicts(setup):
    settings, _, _, _ = setup
    identifier = request_write(setup)
    with TestClient(create_app(settings)) as client:
        assert client.post(f"/api/approvals/{identifier}/reject").json()["status"] == "REJECTED"
        assert client.post(f"/api/approvals/{identifier}/approve").status_code == 409
        assert client.post(f"/api/approvals/{identifier}/reject").status_code == 409
    assert not (settings.workspace_root / "result.txt").exists()


@pytest.mark.parametrize("path", ["../outside.txt", ".env", ".env.local", ".git/config", "C:/outside.txt", "/outside.txt"])
def test_unsafe_write_rejected_before_approval(setup, path):
    _, _, service, _ = setup
    from app.harness.errors import HarnessError
    with pytest.raises(HarnessError):
        service.request(7, "run", "DEVELOPER", "write_text_file", {"path": path, "content": "text"})
    with Session(service.engine) as session:
        assert session.scalars(select(PendingApproval)).all() == []


def test_approve_revalidates_workspace(setup, tmp_path):
    _, _, service, _ = setup
    identifier = request_write(setup)
    other = tmp_path / "other"
    other.mkdir()
    changed_service = ApprovalService(service.engine, WriteTextFile(WorkspacePolicy(other)), setup[0].approval_encryption_key)
    result = asyncio.run(changed_service.approve(identifier))
    assert result["status"] == "FAILED"
    assert not (other / "result.txt").exists()


@pytest.mark.parametrize("approved", [True, False])
def test_approval_interrupt_new_runner_resume_no_replay(setup, approved):
    settings, tools, service, _ = setup
    model = ScriptedModel(write=True)
    first = asyncio.run(WorkflowRunner(handler(model, service), tools, settings).run(TASK))
    assert first.status == "INTERRUPTED"
    assert [node for _, node in model.calls] == ["ANALYZE", "PLAN", "EXECUTE"]
    identifier = first.state["pending_approval_id"]
    assert service.get(identifier)["status"] == "PENDING"
    assert not (settings.workspace_root / "result.txt").exists()
    assert SECRET not in first.model_dump_json()
    with pytest.raises(ApprovalConflict, match="approval_not_resolved"):
        asyncio.run(WorkflowRunner(handler(ScriptedModel(), service), tools, settings).resume(first.thread_id))
    if approved:
        asyncio.run(service.approve(identifier))
    else:
        service.reject(identifier)
    new_engine = create_db_engine(settings.database_url)
    new_service = ApprovalService(new_engine, WriteTextFile(WorkspacePolicy(settings.workspace_root)), settings.approval_encryption_key)
    new_model = ScriptedModel(write=True)
    try:
        result = asyncio.run(WorkflowRunner(handler(new_model, new_service), tools, settings).resume(first.thread_id))
    finally:
        new_engine.dispose()
    assert result.status == ("COMPLETED" if approved else "FAILED")
    assert [node for _, node in new_model.calls] == (["TEST", "REVIEW"] if approved else [])
    assert (result.run_id, result.thread_id) == (first.run_id, first.thread_id)
    assert (settings.workspace_root / "result.txt").exists() is approved
    assert SECRET not in result.model_dump_json()
    events = [event.event_type for event in result.trace]
    assert events.count(TraceEventType.APPROVAL_REQUIRED) == 1
    assert events.count(TraceEventType.WORKFLOW_STARTED) == 1
    assert (TraceEventType.APPROVAL_APPROVED if approved else TraceEventType.APPROVAL_REJECTED) in events
    assert events.index(TraceEventType.APPROVAL_REQUIRED) < events.index(TraceEventType.WORKFLOW_INTERRUPTED) < events.index(TraceEventType.WORKFLOW_RESUMED)
    assert [event.sequence for event in result.trace] == list(range(1, len(result.trace) + 1))
