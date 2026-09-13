from jsonschema import Draft202012Validator
from mcp import types

from app.harness.models import ToolDefinition, ToolExecutionResult
from app.tools.mcp_client import McpClient


class McpToolAdapter:
    def __init__(self, client: McpClient, tool: types.Tool):
        self.client = client
        self.tool_name = tool.name
        self._definition = ToolDefinition(
            name=f"mcp.{tool.name}", description=tool.description or "",
            input_schema=tool.inputSchema,
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition.model_copy(deep=True)

    async def execute(self, arguments: dict[str, object]) -> ToolExecutionResult:
        if not Draft202012Validator(self._definition.input_schema).is_valid(arguments):
            return ToolExecutionResult(success=False, error="invalid_arguments")
        return await self.client.call_tool(self.tool_name, arguments)


async def discover_mcp_tools(client: McpClient) -> list[McpToolAdapter]:
    return [McpToolAdapter(client, tool) for tool in await client.list_tools()]
