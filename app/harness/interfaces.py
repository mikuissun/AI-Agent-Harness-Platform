from typing import Protocol

from app.harness.models import (
    ExecutionStep,
    HarnessAction,
    HarnessContext,
    ToolDefinition,
    ToolExecutionResult,
)


class ModelAdapter(Protocol):
    """Unified model boundary; Stage 2 uses test adapters only."""

    async def generate(
        self,
        context: HarnessContext,
        tools: list[ToolDefinition],
        history: list[ExecutionStep],
    ) -> HarnessAction: ...


class ToolAdapter(Protocol):
    """Registered in-process tool boundary; no CLI / MCP implementation."""

    @property
    def definition(self) -> ToolDefinition: ...

    async def execute(self, arguments: dict[str, object]) -> ToolExecutionResult: ...
