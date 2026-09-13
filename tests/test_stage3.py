import asyncio
import os
from pathlib import Path
import subprocess
import sys
from mcp import StdioServerParameters
import pytest

from app.core.config import Settings
from app.harness.errors import HarnessError
from app.harness.models import FinalAction, HarnessTask, TaskStatus, ToolCallAction
from app.harness.runner import HarnessRunner
from app.tools.cli import create_cli_tools
from app.tools.mcp_adapter import discover_mcp_tools
from app.tools.mcp_client import McpClient
from app.tools.mcp_server import WorkspaceTools
from app.tools.process import run_process
from app.tools.registry import workspace_registry
from app.tools.workspace import WorkspacePolicy


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "sample.txt").write_text("hello workspace\n", encoding="utf-8", newline="")
    (tmp_path / "outside.txt").write_text("outside", encoding="utf-8")
    return root


def settings(root, **kwargs):
    return Settings(_env_file=None, workspace_root=root, **kwargs)


def cli(root, name):
    return next(tool for tool in create_cli_tools(WorkspacePolicy(root), settings(root)) if tool.definition.name == name)


def initialize_git(root):
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True, shell=False)


def test_cli_git_status(workspace):
    initialize_git(workspace)
    result = asyncio.run(cli(workspace, "git_status").execute({}))
    assert result.success
    assert result.data["exit_code"] == 0
    assert "sample.txt" in result.data["stdout"]


def test_cli_nonzero_exit(workspace):
    result = asyncio.run(run_process([sys.executable, "-c", "import sys; print('failure', file=sys.stderr); sys.exit(7)"], workspace, 3, 1000))
    assert not result.success
    assert result.error == "nonzero_exit_code"
    assert result.data["exit_code"] == 7
    assert "failure" in result.data["stderr"]


@pytest.mark.parametrize("path", ["../outside.txt", "absolute"])
def test_cli_workspace_escape(workspace, path):
    path = str(workspace.parent / "outside.txt") if path == "absolute" else path
    result = asyncio.run(cli(workspace, "search_text").execute({"text": "outside", "path": path}))
    assert result.error == "workspace_escape"


def assert_process_dead(pid):
    if os.name == "nt":
        import win32api
        import win32con
        import win32event
        try:
            handle = win32api.OpenProcess(win32con.SYNCHRONIZE, False, pid)
        except Exception:
            return
        try:
            assert win32event.WaitForSingleObject(handle, 1000) == win32con.WAIT_OBJECT_0
        finally:
            handle.Close()
    else:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_cli_timeout_terminates_process_tree(workspace):
    code = "import subprocess,sys,os,time; child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print(os.getpid(), child.pid, flush=True); time.sleep(60)"
    result = asyncio.run(run_process([sys.executable, "-c", code], workspace, 1.5, 1000))
    assert result.error == "cli_timeout"
    assert isinstance(result.data["exit_code"], int)
    pids = [int(value) for value in result.data["stdout"].split()]
    assert len(pids) == 2
    for pid in pids:
        assert_process_dead(pid)


def test_cli_output_truncation(workspace):
    result = asyncio.run(run_process([sys.executable, "-c", "import sys; print('x'*100000); print('y'*100000,file=sys.stderr)"], workspace, 3, 100))
    assert result.success
    assert result.data["truncated"] is True
    assert len(result.data["stdout"]) + len(result.data["stderr"]) == 100


def test_cli_cancellation_terminates_process(workspace):
    pidfile = workspace / "pid.txt"
    code = f"import os,time,pathlib; pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(60)"

    async def scenario():
        task = asyncio.create_task(run_process([sys.executable, "-c", code], workspace, 10, 100))
        try:
            async with asyncio.timeout(5):
                while not pidfile.exists() or not pidfile.read_text():
                    await asyncio.sleep(0.02)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert_process_dead(int(pidfile.read_text()))

    asyncio.run(scenario())


def test_cli_missing_executable(workspace, monkeypatch):
    monkeypatch.setattr("app.tools.cli.shutil.which", lambda _: None)
    result = asyncio.run(cli(workspace, "search_text").execute({"text": "hello"}))
    assert result.error == "executable_not_found"


def test_cli_rejects_shell_parameters(workspace):
    result = asyncio.run(cli(workspace, "git_status").execute({"command": "git push"}))
    assert result.error == "invalid_arguments"
    result = asyncio.run(cli(workspace, "run_pytest").execute({"paths": ["-c"]}))
    assert not result.success


def test_cli_git_diff_staged_arguments(workspace, monkeypatch):
    calls = []

    async def capture(argv, cwd, timeout, limit):
        from app.harness.models import ToolExecutionResult
        calls.append(argv)
        return ToolExecutionResult(success=True)

    monkeypatch.setattr("app.tools.cli.run_process", capture)
    tool = cli(workspace, "git_diff")
    asyncio.run(tool.execute({}))
    asyncio.run(tool.execute({"staged": True}))
    assert "--cached" not in calls[0]
    assert "--cached" in calls[1]
    assert "--no-ext-diff" in calls[1] and "--no-textconv" in calls[1]


def test_cli_search_literal_and_no_match(workspace):
    tool = cli(workspace, "search_text")
    result = asyncio.run(tool.execute({"text": "hello", "path": "sample.txt"}))
    if result.error == "executable_not_found":
        pytest.skip("rg is not installed; missing executable path is tested separately")
    assert result.success and "hello workspace" in result.data["stdout"]
    result = asyncio.run(tool.execute({"text": "--not-an-option; echo secret", "path": "sample.txt"}))
    assert result.success and result.data["exit_code"] == 1


def test_cli_runs_only_selected_pytest(workspace):
    (workspace / "test_selected.py").write_text("def test_ok():\n    assert 2 + 2 == 4\n")
    (workspace / "test_other.py").write_text("def test_bad():\n    assert False\n")
    result = asyncio.run(cli(workspace, "run_pytest").execute({"paths": ["test_selected.py"]}))
    assert result.success, result
    assert "1 passed" in result.data["stdout"]
    assert not (workspace / ".pytest_cache").exists()


def test_cli_does_not_inherit_credentials(workspace, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "secret-value")
    result = asyncio.run(run_process([sys.executable, "-c", "import os; print('DASHSCOPE_API_KEY' in os.environ)"], workspace, 3, 100))
    assert result.success and result.data["stdout"].strip() == "False"


def test_workspace_symlink_escape(workspace):
    link = workspace / "link"
    target = "link"
    try:
        link.symlink_to(workspace.parent / "outside.txt")
    except OSError:
        if os.name != "nt":
            raise
        import _winapi
        _winapi.CreateJunction(str(workspace.parent), str(link))
        target = "link/outside.txt"
    with pytest.raises(HarnessError, match="workspace_escape"):
        WorkspacePolicy(workspace).resolve(target)


def test_workspace_sensitive_path(workspace):
    (workspace / ".env").write_text("API_KEY=secret")
    with pytest.raises(HarnessError, match="sensitive_path_denied"):
        WorkspacePolicy(workspace).resolve(".env")


def test_mcp_initialize_discovery_read_list_and_cleanup(workspace, monkeypatch):
    import mcp.client.stdio as transport
    original = transport._create_platform_compatible_process
    pids = []

    async def capture_process(*args, **kwargs):
        process = await original(*args, **kwargs)
        pids.append(process.pid)
        return process

    monkeypatch.setattr(transport, "_create_platform_compatible_process", capture_process)

    async def scenario():
        client = McpClient(WorkspacePolicy(workspace), settings(workspace))
        async with client:
            adapters = await discover_mcp_tools(client)
            tools = {tool.definition.name: tool for tool in adapters}
            assert set(tools) == {"mcp.workspace_list_files", "mcp.workspace_read_text"}
            assert tools["mcp.workspace_read_text"].definition.input_schema["required"] == ["path"]
            listing = await tools["mcp.workspace_list_files"].execute({})
            assert listing.success
            assert listing.data["files"] == [{"path": "sample.txt", "is_directory": False}]
            read = await tools["mcp.workspace_read_text"].execute({"path": "sample.txt"})
            assert read.success and read.data["text"] == "hello workspace\n"
            assert not read.data["truncated"]
        assert client._session is None and client._stack is None
        assert (await client.call_tool("workspace_read_text", {"path": "sample.txt"})).error == "mcp_not_connected"
        await client.close()  # Idempotent cleanup.
        assert len(pids) == 1
        assert_process_dead(pids[0])

    asyncio.run(scenario())


def test_mcp_escape_unknown_and_server_errors(workspace):
    (workspace / "binary.dat").write_bytes(b"\x00\xff\x00")

    async def scenario():
        async with McpClient(WorkspacePolicy(workspace), settings(workspace)) as client:
            adapters = {tool.tool_name: tool for tool in await discover_mcp_tools(client)}
            read = adapters["workspace_read_text"]
            result = await read.execute({"path": "../outside.txt"})
            assert result.error == "workspace_escape"
            # Bypass client path guard to verify the server independently enforces the policy.
            raw = await client._session.call_tool("workspace_read_text", {"path": "../outside.txt"})
            assert raw.isError and raw.structuredContent["error"] == "workspace_escape"
            assert (await client.call_tool("missing", {})).error == "unknown_tool"
            assert (await read.execute({"path": "binary.dat"})).error == "binary_or_non_utf8_file"
            assert (await read.execute({"path": "."})).error == "not_a_regular_file"
            assert (await read.execute({"path": 10})).error == "invalid_arguments"

    asyncio.run(scenario())


def test_mcp_read_and_list_limits(workspace):
    (workspace / "second.txt").write_text("second")

    async def scenario():
        async with McpClient(WorkspacePolicy(workspace), settings(workspace, mcp_max_read_chars=5, mcp_max_list_files=1)) as client:
            await client.list_tools()
            read = await client.call_tool("workspace_read_text", {"path": "sample.txt"})
            listing = await client.call_tool("workspace_list_files", {})
            assert read.success and read.data == {"text": "hello", "truncated": True}
            assert listing.success and len(listing.data["files"]) == 1 and listing.data["truncated"]

    asyncio.run(scenario())


@pytest.mark.parametrize("mode, expected", [("missing", "mcp_server_start_failed"), ("exit", "mcp_initialize_failed")])
def test_mcp_startup_failures_cleanup(workspace, monkeypatch, mode, expected):
    client = McpClient(WorkspacePolicy(workspace), settings(workspace, cli_timeout_seconds=2))
    params = StdioServerParameters(command="missing-mcp-server-executable") if mode == "missing" else StdioServerParameters(command=sys.executable, args=["-c", "pass"])
    monkeypatch.setattr(client, "_server_parameters", lambda: params)

    async def scenario():
        with pytest.raises(HarnessError, match=expected):
            async with client:
                pytest.fail("Must not open")
        assert client._session is None and client._stack is None

    asyncio.run(scenario())


def test_mcp_list_and_call_failures(workspace):
    class FailedSession:
        async def list_tools(self):
            raise RuntimeError("secret")

        async def call_tool(self, name, arguments):
            raise RuntimeError("secret")

    async def scenario():
        client = McpClient(WorkspacePolicy(workspace), settings(workspace))
        client._session = FailedSession()
        client._tools = {"workspace_list_files": object()}
        with pytest.raises(HarnessError, match="mcp_list_tools_failed"):
            await client.list_tools()
        result = await client.call_tool("workspace_list_files", {})
        assert result.error == "mcp_call_tool_failed"
        assert "secret" not in result.model_dump_json()

    asyncio.run(scenario())


def test_mcp_unreadable_file(workspace, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("secret path")

    monkeypatch.setattr(Path, "open", denied)
    result = WorkspaceTools(WorkspacePolicy(workspace)).call("workspace_read_text", {"path": "sample.txt"})
    assert result.error == "file_unreadable"


def test_mcp_cleanup_failure_is_harness_error(workspace):
    class FailedStack:
        async def aclose(self):
            raise RuntimeError("secret")

    client = McpClient(WorkspacePolicy(workspace), settings(workspace))
    client._stack = FailedStack()
    with pytest.raises(HarnessError, match="mcp_cleanup_failed"):
        asyncio.run(client.close())
    assert client._stack is None and client._session is None


def test_registry_and_harness_cli_mcp_integration(workspace):
    initialize_git(workspace)

    class ScriptedModel:
        def __init__(self, tool, arguments):
            self.tool, self.arguments = tool, arguments

        async def generate(self, context, tools, history):
            if not history:
                return ToolCallAction(tool_name=self.tool, arguments=self.arguments)
            assert history[-1].tool_result.success
            return FinalAction(content="done")

    async def scenario():
        async with workspace_registry(settings(workspace)) as registry:
            assert {definition.name for definition in registry.definitions()} == {
                "git_status", "git_diff", "run_pytest", "search_text",
                "mcp.workspace_list_files", "mcp.workspace_read_text",
            }
            for name, arguments in [("git_status", {}), ("mcp.workspace_read_text", {"path": "sample.txt"})]:
                result = await HarnessRunner(ScriptedModel(name, arguments), registry).run(HarnessTask(id=1, title="test", instruction="inspect"))
                assert result.status == TaskStatus.COMPLETED
                assert result.iterations == 2 and result.output == "done"
                assert any(event.name == name for event in result.trace)
                assert all("hello workspace" not in event.model_dump_json() for event in result.trace)

    asyncio.run(scenario())
