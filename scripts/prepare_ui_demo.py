"""Deterministic UI fixture; no Qwen calls and no automatic approval.

Set UI_DEMO_SCENARIO=read (default) or fix before preparing and serving.
Prepare: python -m scripts.prepare_ui_demo
Serve: python -m uvicorn scripts.prepare_ui_demo:create_demo_app --factory --port 8001
The dedicated demo server must also be used for any later, human-triggered Resume.
"""

from contextlib import asynccontextmanager
import hashlib
import os
import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.agents.roles import AgentResult, ReviewDecision
from app.core.config import Settings
from app.harness.models import FinalAction, ToolCallAction
from app.main import create_app
from app.models.approval import PendingApproval


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = os.environ.get("UI_DEMO_SCENARIO", "read")
if SCENARIO not in {"read", "fix"}:
    raise ValueError("UI_DEMO_SCENARIO must be read or fix")
FOLDER = ROOT / ".demo-runs" / f"portfolio-{SCENARIO}"
WORKSPACE = FOLDER / "workspace"
BUGGY = "def add(a: int, b: int) -> int:\n    return a - b\n"
FIXED = "def add(a: int, b: int) -> int:\n    return a + b\n"


class ScriptedModelAdapter:
    """Demo-only decisions; every tool still goes through the real runtime."""

    def __init__(self, settings):
        self.calls = 0
        self.prompt_tokens = self.completion_tokens = 0

    async def close(self):
        pass

    async def generate(self, context, tools, history):
        self.calls += 1
        phase = context.metadata["phase"]
        if phase == "ANALYZE" and not history:
            return ToolCallAction(tool_name="mcp.workspace_list_files", arguments={})
        if phase == "ANALYZE" and len(history) == 1:
            return ToolCallAction(tool_name="mcp.workspace_read_text", arguments={"path": "calculator.py"})
        if phase == "ANALYZE":
            result = AgentResult(success=True, output="已读取项目文件列表和 Calculator 源码，将结合对应测试完成检查。")
        elif phase == "PLAN":
            result = AgentResult(success=True, output="已制定执行计划", plan=(["读取对应测试，确认检查范围", "运行测试并审查结果，不修改文件"] if SCENARIO == "read" else ["通过 write_text_file 请求修改 calculator.py，等待人工审批", "审批后运行对应测试并审查结果"]))
        elif phase == "EXECUTE":
            if SCENARIO == "fix":
                return ToolCallAction(tool_name="write_text_file", arguments={"path": "calculator.py", "content": FIXED})
            if not history:
                return ToolCallAction(tool_name="mcp.workspace_read_text", arguments={"path": "tests/test_calculator.py"})
            result = AgentResult(success=True, output="已读取对应测试，检查过程未修改任何文件。")
        elif phase == "TEST":
            if not history:
                return ToolCallAction(tool_name="run_pytest", arguments={"paths": ["tests/"]})
            passed = history[-1].tool_result.success
            result = AgentResult(success=passed, output="真实 pytest PASS" if passed else "真实 pytest FAILED")
        elif phase == "REVIEW":
            passed = context.metadata["workflow_state"].get("test_result", {}).get("success") is True
            result = AgentResult(success=passed, output=(("Calculator 项目测试已通过，当前实现与测试配置正常。" if SCENARIO == "read" else "修复已完成，测试通过，审批后的写入已成功执行。") if passed else "测试未通过，检查未完成。"), decision=ReviewDecision.PASS if passed else ReviewDecision.FAILED)
        else:
            raise ValueError("Unsupported demo phase; no automatic retry")
        return FinalAction(content=result.model_dump_json())


def create_demo_app():
    settings = Settings().model_copy(update={
        "workspace_root": WORKSPACE,
        "database_url": f"sqlite:///{(FOLDER / 'tasks.db').as_posix()}",
        "checkpoint_database_url": f"sqlite:///{(FOLDER / 'checkpoints.db').as_posix()}",
        "workflow_max_iterations": 1, "retry_max_attempts": 1,
    })
    application = create_app(settings)
    original = application.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        async with original(app):
            app.state.runs.model_factory = ScriptedModelAdapter
            yield

    application.router.lifespan_context = lifespan
    return application


def main():
    # Never reset an existing checkpoint or overwrite a previously prepared run.
    if FOLDER.exists():
        raise SystemExit("Demo already exists; open its report.json instead of rerunning.")
    (WORKSPACE / "tests").mkdir(parents=True)
    (WORKSPACE / "calculator.py").write_text(FIXED if SCENARIO == "read" else BUGGY, encoding="utf-8")
    (WORKSPACE / "tests" / "test_calculator.py").write_text(
        "from calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n    assert add(-1, 1) == 0\n", encoding="utf-8")
    (WORKSPACE / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\npythonpath = ["."]\ntestpaths = ["tests"]\n', encoding="utf-8")
    before = hashlib.sha256((WORKSPACE / "calculator.py").read_bytes()).hexdigest()
    application = create_demo_app()
    with TestClient(application) as client:
        response = client.post("/api/tasks", json=({"title": "检查 Calculator 测试", "instruction": "检查 demo workspace 中 Calculator 项目的当前测试状态，读取必要文件并运行测试，最后给出检查结果。不要修改任何文件。"} if SCENARIO == "read" else {"title": "修复 Calculator 实现", "instruction": "检查 calculator.py 与对应测试，修复导致测试失败的问题，并确保测试通过。任何文件写入必须经过人工审批。"}))
        response.raise_for_status()
        task = response.json()
        response = client.post(f"/api/tasks/{task['id']}/run")
        response.raise_for_status()
        run = response.json()
        (FOLDER / "report.json").write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
        if SCENARIO == "read":
            assert run["status"] == "COMPLETED", run.get("error")
            assert hashlib.sha256((WORKSPACE / "calculator.py").read_bytes()).hexdigest() == before
            assert not run["pending_approval_id"]
            calls = [event["name"] for event in run["trace"] if event["event_type"] == "TOOL_CALLED"]
            assert calls.count("run_pytest") == 1
            assert "mcp.workspace_list_files" in calls and "mcp.workspace_read_text" in calls
            assert "write_text_file" not in calls
            print(json.dumps({key: run[key] for key in ("run_id", "thread_id", "status", "output")}, ensure_ascii=False))
            return
        assert run["status"] == "INTERRUPTED", run.get("error")
        identifier = run["pending_approval_id"]
        response = client.get(f"/api/approvals/{identifier}")
        response.raise_for_status()
        approval = response.json()
        assert approval["status"] == "PENDING"
        assert approval["tool_name"] == "write_text_file" and approval["risk_level"] == "WRITE"
        assert approval["arguments_summary"]["path"] == "calculator.py"
        assert any(event["event_type"] == "APPROVAL_REQUIRED" for event in run["trace"])
        assert not any(event["name"] in {"TEST", "run_pytest"} for event in run["trace"])
        unchanged = hashlib.sha256((WORKSPACE / "calculator.py").read_bytes()).hexdigest() == before
        assert unchanged
        # Read-only verification; payload creation/encryption belongs to ApprovalService.
        with Session(application.state.engine) as session:
            row = session.get(PendingApproval, identifier)
            encrypted = bool(row.encrypted_payload) and FIXED.encode() not in row.encrypted_payload
            assert encrypted
        run.update(demo_model="ScriptedModelAdapter", before_sha256=before,
                   approval_status=approval["status"], unchanged_before_approval=unchanged,
                   encrypted_payload_persisted=encrypted)
        (FOLDER / "report.json").write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({key: run[key] for key in ("run_id", "thread_id", "pending_approval_id", "status", "approval_status", "unchanged_before_approval", "encrypted_payload_persisted")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
