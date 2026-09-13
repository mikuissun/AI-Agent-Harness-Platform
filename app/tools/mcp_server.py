"""Local read-only MCP server. stdout is reserved for the SDK transport."""

import argparse
import asyncio
import os
import stat

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from pydantic import BaseModel, ConfigDict, ValidationError

from app.harness.errors import HarnessError
from app.harness.models import ToolExecutionResult
from app.tools.workspace import WorkspacePolicy


class ListArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = "."


class ReadArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str


class WorkspaceTools:
    def __init__(self, policy: WorkspacePolicy, max_read_chars: int = 20000, max_list_files: int = 200):
        if max_read_chars < 1 or max_list_files < 1:
            raise HarnessError("invalid_mcp_limits")
        self.policy = policy
        self.max_read_chars = max_read_chars
        self.max_list_files = max_list_files
        self._handlers = {
            "workspace_list_files": (ListArguments, self.list_files, "List entries in a workspace directory"),
            "workspace_read_text": (ReadArguments, self.read_text, "Read a UTF-8 workspace text file"),
        }

    def definitions(self) -> list[types.Tool]:
        return [types.Tool(name=name, description=description, inputSchema=model.model_json_schema())
                for name, (model, _, description) in self._handlers.items()]

    def list_files(self, payload: ListArguments) -> dict:
        directory = self.policy.resolve(payload.path, directory=True)
        entries = []
        truncated = False
        with os.scandir(directory) as iterator:
            for entry in iterator:
                try:
                    path = self.policy.resolve(entry.path)
                except HarnessError:
                    continue  # Do not reveal escaped symlink targets or credential entries.
                if len(entries) == self.max_list_files:
                    truncated = True
                    break
                entries.append({
                    "path": path.relative_to(self.policy.root).as_posix(),
                    "is_directory": path.is_dir(),
                })
        return {"files": sorted(entries, key=lambda entry: entry["path"]), "truncated": truncated}

    def read_text(self, payload: ReadArguments) -> dict:
        path = self.policy.resolve(payload.path)
        if not stat.S_ISREG(path.stat().st_mode):
            raise HarnessError("not_a_regular_file")
        # Read a bounded UTF-8 prefix, never the entire file into memory.
        with path.open("r", encoding="utf-8", newline="") as stream:
            content = stream.read(self.max_read_chars + 1)
        if any(ord(char) < 32 and char not in "\n\r\t" for char in content):
            raise HarnessError("binary_or_non_utf8_file")
        return {"text": content[:self.max_read_chars], "truncated": len(content) > self.max_read_chars}

    def call(self, name: str, arguments: dict) -> ToolExecutionResult:
        if name not in self._handlers:
            return ToolExecutionResult(success=False, error="unknown_tool")
        model, handler, _ = self._handlers[name]
        try:
            return ToolExecutionResult(success=True, data=handler(model.model_validate(arguments)))
        except ValidationError:
            return ToolExecutionResult(success=False, error="invalid_arguments")
        except UnicodeError:
            return ToolExecutionResult(success=False, error="binary_or_non_utf8_file")
        except HarnessError as exc:
            return ToolExecutionResult(success=False, error=str(exc))
        except OSError:
            return ToolExecutionResult(success=False, error="file_unreadable")


def create_server(tools: WorkspaceTools) -> Server:
    server = Server("agent-harness-workspace")

    @server.list_tools()
    async def list_tools():
        return tools.definitions()

    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        # Own strict validation yields safe codes instead of SDK errors containing input values.
        result = tools.call(name, arguments)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=result.model_dump_json())],
            structuredContent=result.model_dump(mode="json"), isError=not result.success,
        )

    return server


async def serve(tools: WorkspaceTools) -> None:
    server = create_server(tools)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--max-read-chars", type=int, default=20000)
    parser.add_argument("--max-list-files", type=int, default=200)
    args = parser.parse_args()
    tools = WorkspaceTools(WorkspacePolicy(args.workspace_root), args.max_read_chars, args.max_list_files)
    asyncio.run(serve(tools))


if __name__ == "__main__":
    main()
