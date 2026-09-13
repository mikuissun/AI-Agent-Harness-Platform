from contextlib import asynccontextmanager

from app.core.config import Settings
from app.harness.registry import ToolRegistry
from app.tools.cli import create_cli_tools
from app.tools.mcp_adapter import discover_mcp_tools
from app.tools.mcp_client import McpClient
from app.tools.workspace import WorkspacePolicy


@asynccontextmanager
async def workspace_registry(settings: Settings):
    policy = WorkspacePolicy(settings.workspace_root)
    registry = ToolRegistry()
    for tool in create_cli_tools(policy, settings):
        registry.register(tool)
    async with McpClient(policy, settings) as client:
        for tool in await discover_mcp_tools(client):
            registry.register(tool)
        yield registry
