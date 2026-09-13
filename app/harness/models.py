from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class HarnessTask(BaseModel):
    id: int
    title: str
    instruction: str


class HarnessState(BaseModel):
    task: HarnessTask
    status: TaskStatus = TaskStatus.PENDING


class HarnessContext(BaseModel):
    task_id: int
    run_id: str
    instruction: str
    iteration: int = Field(default=0, ge=0)
    max_iterations: int = Field(default=8, ge=1)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ActionType(StrEnum):
    FINAL = "FINAL"
    TOOL_CALL = "TOOL_CALL"


class FinalAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal[ActionType.FINAL] = ActionType.FINAL
    content: str


class ToolCallAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal[ActionType.TOOL_CALL] = ActionType.TOOL_CALL
    tool_name: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]*$")
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


HarnessAction = Annotated[FinalAction | ToolCallAction, Field(discriminator="type")]


class ToolRiskLevel(StrEnum):
    READ_ONLY = "READ_ONLY"
    SAFE_EXECUTION = "SAFE_EXECUTION"
    WRITE = "WRITE"
    PRIVILEGED = "PRIVILEGED"


class ToolDefinition(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]*$")
    description: str
    input_schema: dict[str, JsonValue]
    risk_level: ToolRiskLevel = ToolRiskLevel.READ_ONLY


class ToolExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    success: bool
    data: JsonValue = None
    error: str | None = None


class ExecutionStep(BaseModel):
    action: HarnessAction
    tool_result: ToolExecutionResult


class TraceEventType(StrEnum):
    RUN_STARTED = "RUN_STARTED"
    MODEL_CALLED = "MODEL_CALLED"
    TOOL_CALLED = "TOOL_CALLED"
    TOOL_COMPLETED = "TOOL_COMPLETED"
    RUN_COMPLETED = "RUN_COMPLETED"
    RUN_FAILED = "RUN_FAILED"
    WORKFLOW_STARTED = "WORKFLOW_STARTED"
    NODE_STARTED = "NODE_STARTED"
    NODE_COMPLETED = "NODE_COMPLETED"
    WORKFLOW_INTERRUPTED = "WORKFLOW_INTERRUPTED"
    WORKFLOW_RESUMED = "WORKFLOW_RESUMED"
    WORKFLOW_COMPLETED = "WORKFLOW_COMPLETED"
    WORKFLOW_FAILED = "WORKFLOW_FAILED"
    AGENT_STARTED = "AGENT_STARTED"
    AGENT_COMPLETED = "AGENT_COMPLETED"
    RETRY_STARTED = "RETRY_STARTED"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_APPROVED = "APPROVAL_APPROVED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    PERMISSION_DENIED = "PERMISSION_DENIED"


class TraceEvent(BaseModel):
    sequence: int
    iteration: int
    event_type: TraceEventType
    name: str
    success: bool
    summary: str


class HarnessRunResult(BaseModel):
    task_id: int
    run_id: str
    status: Literal[TaskStatus.COMPLETED, TaskStatus.FAILED]
    output: str | None = None
    error: str | None = None
    iterations: int
    trace: list[TraceEvent] = Field(default_factory=list)
