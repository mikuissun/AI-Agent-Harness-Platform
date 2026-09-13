import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import Settings
from app.harness.errors import HarnessError
from app.harness.models import ToolDefinition, ToolExecutionResult
from app.tools.process import run_process
from app.tools.workspace import WorkspacePolicy


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class DiffArguments(Arguments):
    staged: bool = False


class PytestArguments(Arguments):
    paths: list[str] = Field(min_length=1, max_length=20)


class SearchArguments(Arguments):
    text: str = Field(min_length=1, max_length=1000, pattern=r"^[^\x00]+$")
    path: str = "."


@dataclass(frozen=True)
class CliToolSpec:
    name: str
    description: str
    executable: str
    base_args: tuple[str, ...]
    arguments_model: type[Arguments]
    timeout_seconds: float

    @property
    def input_schema(self):
        return self.arguments_model.model_json_schema()


class CliToolAdapter:
    """Specs come only from the fixed factory; callers supply schema-checked arguments."""

    def __init__(self, spec: CliToolSpec, policy: WorkspacePolicy, max_output_chars: int = 20000):
        self.spec = spec
        self.policy = policy
        self.max_output_chars = max_output_chars

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(name=self.spec.name, description=self.spec.description, input_schema=self.spec.input_schema)

    def _arguments(self, payload: Arguments) -> list[str]:
        if isinstance(payload, DiffArguments):
            # Disable repository-configured external diff/textconv and exclude common credentials.
            return (["--cached"] if payload.staged else []) + [
                "--", ".", ":(exclude,glob)**/.env", ":(exclude,glob)**/.env.*",
                ":(exclude,glob)**/*.pem", ":(exclude,glob)**/*.key",
            ]
        if isinstance(payload, PytestArguments):
            paths = []
            for value in payload.paths:
                path = self.policy.resolve(value)
                if path.is_file() and not (path.name.startswith("test_") and path.suffix == ".py"):
                    raise HarnessError("invalid_test_path")
                if not path.is_file() and not path.is_dir():
                    raise HarnessError("invalid_test_path")
                if path.is_dir():
                    for parent, directories, files in os.walk(path, followlinks=False):
                        for name in directories + files:
                            child = Path(parent) / name
                            if child.is_symlink() or child.is_junction():
                                raise HarnessError("linked_test_path_denied")
                paths.append(str(path))
            return ["--", *paths]
        if isinstance(payload, SearchArguments):
            path = self.policy.resolve(payload.path)
            return ["--", payload.text, str(path)]
        return []

    async def execute(self, arguments: dict[str, object]) -> ToolExecutionResult:
        try:
            payload = self.spec.arguments_model.model_validate(arguments)
            cwd = self.policy.resolve(directory=True)
            args = self._arguments(payload)
        except ValidationError:
            return ToolExecutionResult(success=False, error="invalid_arguments")
        except HarnessError as exc:
            return ToolExecutionResult(success=False, error=str(exc))
        except OSError:
            return ToolExecutionResult(success=False, error="path_unavailable")
        executable = shutil.which(self.spec.executable)
        if executable is None:
            return ToolExecutionResult(success=False, error="executable_not_found")
        if os.name == "nt" and Path(executable).suffix.lower() != ".exe":
            return ToolExecutionResult(success=False, error="executable_not_allowed")
        result = await run_process(
            [executable, *self.spec.base_args, *args], cwd,
            self.spec.timeout_seconds, self.max_output_chars,
        )
        # rg exit 1 is a successful search with no matches.
        if self.spec.name == "search_text" and result.error == "nonzero_exit_code" and result.data["exit_code"] == 1:
            return ToolExecutionResult(success=True, data=result.data)
        return result


def create_cli_tools(policy: WorkspacePolicy, settings: Settings) -> list[CliToolAdapter]:
    specs = [
        ("git_status", "Show workspace Git status", "git", ("--no-pager", "-c", "core.fsmonitor=false", "status", "--short", "--ignore-submodules=all", "--", "."), Arguments),
        ("git_diff", "Show workspace Git diff", "git", ("--no-pager", "-c", "core.fsmonitor=false", "diff", "--no-ext-diff", "--no-textconv", "--no-color", "--ignore-submodules=all"), DiffArguments),
        ("run_pytest", "Run specified trusted workspace tests", sys.executable, ("-I", "-B", "-m", "pytest", "-o", "addopts=", "-p", "no:cacheprovider"), PytestArguments),
        ("search_text", "Search literal text within the workspace", "rg", ("--no-config", "--fixed-strings", "--line-number", "--color", "never", "--no-follow", "--glob", "!.env*", "--glob", "!*.pem", "--glob", "!*.key"), SearchArguments),
    ]
    return [CliToolAdapter(CliToolSpec(*spec, settings.cli_timeout_seconds), policy, settings.cli_max_output_chars) for spec in specs]
