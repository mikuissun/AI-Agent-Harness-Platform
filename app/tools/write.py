import hashlib
from pathlib import Path, PureWindowsPath

from pydantic import BaseModel, ConfigDict

from app.harness.errors import HarnessError
from app.harness.models import ToolDefinition, ToolExecutionResult, ToolRiskLevel
from app.tools.workspace import WorkspacePolicy


class WriteArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str
    content: str


class WriteTextFile:
    """Raw adapter held by ApprovalService; registry callers receive ApprovalGuard."""

    def __init__(self, policy: WorkspacePolicy, max_chars: int = 20000):
        self.policy, self.max_chars = policy, max_chars

    @property
    def definition(self) -> ToolDefinition:
        schema = WriteArguments.model_json_schema()
        schema["properties"]["content"]["maxLength"] = self.max_chars
        return ToolDefinition(name="write_text_file", description="Write UTF-8 text after human approval",
                              input_schema=schema, risk_level=ToolRiskLevel.WRITE)

    def validate(self, arguments: dict) -> tuple[Path, WriteArguments]:
        try:
            payload = WriteArguments.model_validate(arguments)
        except Exception:
            raise HarnessError("invalid_arguments") from None
        path = Path(payload.path)
        windows = PureWindowsPath(payload.path)
        if not payload.path or "\0" in payload.path or path.is_absolute() or windows.drive or windows.root or ":" in payload.path:
            raise HarnessError("invalid_write_path")
        if len(payload.content) > self.max_chars or "\0" in payload.content:
            raise HarnessError("invalid_write_content")
        try:
            payload.content.encode("utf-8")
        except UnicodeError:
            raise HarnessError("invalid_write_content") from None
        if ".." in path.parts or ".." in windows.parts:
            raise HarnessError("workspace_escape")
        if any(self.policy.is_sensitive(part) or part.lower().startswith(".env") for part in path.parts):
            raise HarnessError("sensitive_path_denied")
        if path.name.lower().endswith((".db", ".sqlite", ".sqlite3", ".approval-key")):
            raise HarnessError("sensitive_path_denied")
        # New files are allowed, but parent directories must already exist and pass shared policy.
        parent = self.policy.resolve(str(path.parent), directory=True)
        target = parent / path.name
        if target.is_symlink() or target.is_junction():
            raise HarnessError("linked_write_path_denied")
        if target.exists():
            target = self.policy.resolve(str(target))
            if not target.is_file() or target.stat().st_nlink != 1:
                raise HarnessError("invalid_write_target")
        return target, payload

    async def execute(self, arguments: dict[str, object]) -> ToolExecutionResult:
        try:
            path, payload = self.validate(arguments)
            with path.open("w", encoding="utf-8", newline="") as stream:
                stream.write(payload.content)
            return ToolExecutionResult(success=True, data={"characters": len(payload.content), "sha256": hashlib.sha256(payload.content.encode()).hexdigest()})
        except HarnessError as exc:
            return ToolExecutionResult(success=False, error=str(exc))
        except OSError:
            return ToolExecutionResult(success=False, error="write_failed")
