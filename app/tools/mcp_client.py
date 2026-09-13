import asyncio
import os
import sys
from contextlib import AsyncExitStack
from datetime import timedelta

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from app.core.config import Settings
from app.harness.errors import HarnessError
from app.harness.models import ToolExecutionResult
from app.tools.process import safe_environment
from app.tools.workspace import WorkspacePolicy


class McpClient:
    """Own a single local SDK session. Enter and close in the same async task."""

    def __init__(self, policy: WorkspacePolicy, settings: Settings):
        self.policy = policy
        self.settings = settings
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._tools: dict[str, types.Tool] = {}

    def _server_parameters(self) -> StdioServerParameters:
        return StdioServerParameters(
            command=sys.executable,
            args=["-I", "-B", "-m", "app.tools.mcp_server",
                  "--workspace-root", str(self.policy.root),
                  "--max-read-chars", str(self.settings.mcp_max_read_chars),
                  "--max-list-files", str(self.settings.mcp_max_list_files)],
            cwd=self.policy.resolve(directory=True), env=safe_environment(),
        )

    async def __aenter__(self):
        if self._stack is not None:
            raise HarnessError("mcp_already_open")
        self._stack = AsyncExitStack()
        phase = "mcp_server_start_failed"
        try:
            errlog = self._stack.enter_context(open(os.devnull, "w"))
            read, write = await self._stack.enter_async_context(stdio_client(self._server_parameters(), errlog=errlog))
            phase = "mcp_initialize_failed"
            self._session = await self._stack.enter_async_context(ClientSession(
                read, write, read_timeout_seconds=timedelta(seconds=self.settings.cli_timeout_seconds),
            ))
            await self._session.initialize()
            return self
        except BaseException as exc:
            try:
                await self.close()
            except HarnessError:
                pass
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise HarnessError(phase) from None

    async def __aexit__(self, exc_type, exc, traceback):
        await self.close()

    async def close(self) -> None:
        stack, self._stack = self._stack, None
        self._session = None
        self._tools = {}
        if stack is not None:
            try:
                await stack.aclose()
            except Exception:
                raise HarnessError("mcp_cleanup_failed") from None

    async def list_tools(self) -> list[types.Tool]:
        if self._session is None:
            raise HarnessError("mcp_not_connected")
        try:
            result = await self._session.list_tools()
            self._tools = {tool.name: tool for tool in result.tools}
            return [tool.model_copy(deep=True) for tool in result.tools]
        except Exception:
            raise HarnessError("mcp_list_tools_failed") from None

    async def call_tool(self, name: str, arguments: dict) -> ToolExecutionResult:
        if self._session is None:
            return ToolExecutionResult(success=False, error="mcp_not_connected")
        if name not in self._tools:
            return ToolExecutionResult(success=False, error="unknown_tool")
        try:
            # Shared policy is checked here and again by the server; neither trusts the other.
            if "path" in arguments:
                self.policy.resolve(arguments["path"])
        except HarnessError as exc:
            return ToolExecutionResult(success=False, error=str(exc))
        try:
            result = await self._session.call_tool(name, arguments)
            if result.structuredContent is None:
                return ToolExecutionResult(success=False, error="mcp_server_tool_error" if result.isError else "invalid_mcp_result")
            converted = ToolExecutionResult.model_validate(result.structuredContent)
            if result.isError and converted.success:
                return ToolExecutionResult(success=False, error="mcp_server_tool_error")
            return converted
        except Exception:
            return ToolExecutionResult(success=False, error="mcp_call_tool_failed")
