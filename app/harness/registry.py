from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from app.harness.errors import DuplicateToolError, HarnessError, UnknownToolError
from app.harness.interfaces import ToolAdapter
from app.harness.models import ToolDefinition, ToolExecutionResult, ToolRiskLevel


class ApprovalGuard:
    """Registry access cannot directly execute a registered high-risk adapter."""

    def __init__(self, definition: ToolDefinition):
        self.definition = definition.model_copy(deep=True)

    async def execute(self, arguments: dict[str, object]) -> ToolExecutionResult:
        return ToolExecutionResult(success=False, error="permission_denied")


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolAdapter] = {}
        self._definitions: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolAdapter) -> None:
        definition = tool.definition.model_copy(deep=True)
        if definition.name in self._tools:
            raise DuplicateToolError()
        try:
            Draft202012Validator.check_schema(definition.input_schema)
        except SchemaError:
            raise HarnessError("invalid_tool_schema") from None
        # External references could trigger network/file access during validation.
        def has_reference(value: object) -> bool:
            if isinstance(value, dict):
                return any(
                    key in {"$ref", "$dynamicRef"} or has_reference(item)
                    for key, item in value.items()
                )
            return isinstance(value, list) and any(has_reference(item) for item in value)

        if has_reference(definition.input_schema):
            raise HarnessError("tool_schema_references_not_supported")
        self._tools[definition.name] = tool
        self._definitions[definition.name] = definition

    def get(self, name: str) -> ToolAdapter:
        try:
            if self._definitions[name].risk_level in {ToolRiskLevel.WRITE, ToolRiskLevel.PRIVILEGED}:
                return ApprovalGuard(self._definitions[name])
            return self._tools[name]
        except KeyError:
            raise UnknownToolError() from None

    def definitions(self) -> list[ToolDefinition]:
        return [item.model_copy(deep=True) for item in self._definitions.values()]

    def validate_arguments(self, name: str, arguments: dict[str, object]) -> bool:
        self.get(name)
        return Draft202012Validator(self._definitions[name].input_schema).is_valid(arguments)
