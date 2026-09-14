"""Explicit real-Qwen scenarios, never collected by pytest. Run once per scenario."""

import argparse
import asyncio
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.adapters.qwen import QwenModelAdapter
from app.workflow.checkpoint import open_checkpointer


TASKS = {
    "read": "Inspect the demo workspace and report its current test status. Do not write or change any file. Use MCP to inspect files and have Tester run tests/.",
    "fix": "修复 calculator.py 中导致测试失败的问题，并确保测试通过。",
    "reject": "Add a short module docstring to calculator.py without changing add() behavior. Read calculator.py via MCP, then request write_text_file with the complete file. Do not modify other files.",
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FixDemoModel(QwenModelAdapter):
    """Real Qwen with narrower demo tool visibility; normal governance is unchanged."""

    def __init__(self, settings):
        super().__init__(settings)
        self.phases = []
        self.pytest_results = []

    async def generate(self, context, tools, history):
        self.phases.append(context.metadata["phase"])
        if context.metadata["role"] == "DEVELOPER":
            tools = [tool for tool in tools if tool.name != "run_pytest"]
        for step in history:
            if step.action.tool_name == "run_pytest":
                data = step.tool_result.data or {}
                self.pytest_results.append({"success": step.tool_result.success,
                    "exit_code": data.get("exit_code"), "stdout": data.get("stdout", "")[-1500:]})
        return await super().generate(context, tools, history)


async def checkpoint_evidence(settings, thread_id):
    async with open_checkpointer(settings.checkpoint_database_url) as saver:
        saved = await saver.aget({"configurable": {"thread_id": thread_id}})
        state = saved["channel_values"]
        return {key: state.get(key) for key in ("review_decision", "test_result")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=TASKS)
    parser.add_argument("--attempt", type=int, default=1, help="Separate evidence directory for a necessary failed-scenario retry")
    parser.add_argument("--resume", action="store_true", help="Continue existing pending checkpoint only; do not re-run model stages")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    folder = root / ".demo-runs" / (args.scenario if args.attempt == 1 else f"{args.scenario}-{args.attempt}")
    workspace = folder / "workspace"
    if not args.resume:
        if folder.exists():
            raise SystemExit("Scenario directory already exists; use --resume for a pending run. Completed runs are not repeated.")
        shutil.copytree(root / "demo_workspace", workspace)
        if args.scenario == "fix":
            (workspace / "calculator.py").write_text("def add(a: int, b: int) -> int:\n    return a - b\n", encoding="utf-8")
        subprocess.run(["git", "init", str(workspace)], check=True, capture_output=True, shell=False)
    settings = Settings().model_copy(update={"workspace_root": workspace,
        "database_url": f"sqlite:///{(folder / 'tasks.db').as_posix()}",
        "checkpoint_database_url": f"sqlite:///{(folder / 'checkpoints.db').as_posix()}"})
    if args.scenario == "fix":
        settings = settings.model_copy(update={"workflow_max_iterations": 1, "retry_max_attempts": 1})
    report_path = folder / "report.json"
    models = []
    application = create_app(settings)
    with TestClient(application) as client:
        if args.scenario in {"fix", "reject"}:
            def model_factory(config):
                model = FixDemoModel(config)
                models.append(model)
                return model
            application.state.runs.model_factory = model_factory
        if args.resume:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            current = client.get(f"/api/runs/{report['run_id']}").json()
            if current["status"] != "INTERRUPTED":
                raise SystemExit("Only interrupted runs can be continued; completed/failed runs are not repeated.")
        else:
            report = {"scenario": args.scenario, "task": TASKS[args.scenario], "before_sha256": digest(workspace / "calculator.py")}
            task = client.post("/api/tasks", json={"title": args.scenario, "instruction": TASKS[args.scenario]}).json()
            response = client.post(f"/api/tasks/{task['id']}/run")
            if response.status_code != 200:
                report.update(status="API_FAILED", http_status=response.status_code)
                report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
                raise SystemExit("Run API failed; inspect safe report. No retry performed.")
            current = response.json()
        report.update(run_id=current["run_id"], thread_id=current["thread_id"], status=current["status"], error=current["error"], usage=current["usage"])
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        if current["status"] == "INTERRUPTED" and args.scenario != "read":
            identifier = current["pending_approval_id"]
            approval = client.get(f"/api/approvals/{identifier}").json()
            unchanged = digest(workspace / "calculator.py") == report["before_sha256"]
            assert unchanged, "File changed before approval"
            assert approval["tool_name"] == "write_text_file" and approval["arguments_summary"]["path"] == "calculator.py"
            report.update(approval_id=identifier, unchanged_before_approval=unchanged)
            if args.scenario == "reject":
                assert identifier and approval["status"] == "PENDING"
                report.update(pending_approval_id=identifier, status_before_approval=current["status"],
                    approval_status_before=approval["status"], trace_before_resume=current["trace"],
                    calls_before_reject=current["usage"]["calls"])
                report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            if args.scenario == "fix":
                assert identifier and approval["status"] == "PENDING"
                assert "return a - b" in (workspace / "calculator.py").read_text(encoding="utf-8")
                assert not any(event["name"] == "run_pytest" for event in current["trace"])
                report.update(pending_approval_id=identifier, status_before_approval=current["status"],
                    approval_status_before=approval["status"], trace_before_resume=current["trace"])
                report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            decision = "reject" if args.scenario == "reject" else "approve"
            if approval["status"] == "PENDING":
                decided = client.post(f"/api/approvals/{identifier}/{decision}")
                assert decided.status_code == 200
                if args.scenario == "reject":
                    report.update(approval_status_after=decided.json()["status"],
                        rejection_result=decided.json()["result"],
                        unchanged_after_reject=digest(workspace / "calculator.py") == report["before_sha256"])
                    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
                    assert report["approval_status_after"] == "REJECTED" and report["unchanged_after_reject"]
                if args.scenario == "fix":
                    report.update(approval_status_after=decided.json()["status"],
                        changed_after_approve=digest(workspace / "calculator.py") != report["before_sha256"])
                    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
                    assert report["approval_status_after"] == "EXECUTED" and report["changed_after_approve"]
            response = client.post(f"/api/runs/{current['run_id']}/resume")
            assert response.status_code == 200
            current = response.json()
            if args.scenario in {"fix", "reject"}:
                assert current["thread_id"] == report["thread_id"] and current["run_id"] == report["run_id"]
                previous_trace = report.pop("trace_before_resume")
                assert current["trace"][:len(previous_trace)] == previous_trace
                resumed_nodes = [event["name"] for event in current["trace"][len(previous_trace):] if event["event_type"] == "NODE_STARTED"]
                report.update(same_checkpoint_resumed=True, resumed_nodes=resumed_nodes,
                    completed_nodes_not_replayed=not any(node in {"ANALYZE", "PLAN", "EXECUTE"} for node in resumed_nodes))
        report.update(status=current["status"], error=current["error"], usage=current["usage"],
            tools_called=[event["name"] for event in current["trace"] if event["event_type"] == "TOOL_CALLED"],
            workflow_path=[event["name"] for event in current["trace"] if event["event_type"] == "NODE_STARTED"],
            events=[event["event_type"] for event in current["trace"]],
            unchanged_after=digest(workspace / "calculator.py") == report["before_sha256"])
        if args.scenario == "fix":
            report.update(asyncio.run(checkpoint_evidence(settings, current["thread_id"])))
            report["tool_counts"] = dict(Counter(report["tools_called"]))
            report["write_requests"] = report["events"].count("APPROVAL_REQUIRED")
            report["write_executions"] = int(report.get("approval_status_after") == "EXECUTED")
            report["pytest_results"] = [result for model in models for result in model.pytest_results]
        if args.scenario == "reject":
            report["tool_counts"] = dict(Counter(report["tools_called"]))
            report["write_requests"] = report["events"].count("APPROVAL_REQUIRED")
            report["write_executions"] = 0 if report.get("approval_status_after") == "REJECTED" else None
            report["no_rejection_retry"] = ("RETRY_STARTED" not in report["events"]
                and report.get("calls_before_reject") == current["usage"]["calls"]
                and report["write_requests"] == 1)
            report["after_sha256"] = digest(workspace / "calculator.py")
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
        expected = "FAILED" if args.scenario == "reject" else "COMPLETED"
        assert current["status"] == expected, "Scenario did not reach expected status; no automatic rerun"
        if args.scenario == "fix":
            assert report["write_requests"] == report["write_executions"] == 1
            assert report["completed_nodes_not_replayed"] and report["resumed_nodes"] == ["TEST", "REVIEW"]
            assert report["tool_counts"].get("run_pytest") == 1 and report["review_decision"] == "PASS"
            assert len(report["pytest_results"]) == 1 and report["pytest_results"][0]["success"]
            assert report["pytest_results"][0]["exit_code"] == 0
        if args.scenario == "reject":
            assert report["unchanged_after"] and report.get("approval_id")
            assert report["approval_status_after"] == "REJECTED" and report["unchanged_after_reject"]
            assert report["same_checkpoint_resumed"] and report["completed_nodes_not_replayed"]
            assert report["resumed_nodes"] == [] and report["no_rejection_retry"]
            assert report["rejection_result"]["success"] is False
            assert report["rejection_result"]["error"] == "permission_denied"
            assert "APPROVAL_REJECTED" in report["events"] and "WORKFLOW_RESUMED" in report["events"]
        if args.scenario == "read":
            assert report["unchanged_after"] and "APPROVAL_REQUIRED" not in report["events"]
            assert report["tools_called"] and "write_text_file" not in report["tools_called"]


if __name__ == "__main__":
    main()
