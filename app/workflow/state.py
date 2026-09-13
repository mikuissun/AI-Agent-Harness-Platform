from enum import StrEnum
from typing import Annotated, Literal, NotRequired, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from app.harness.models import ToolExecutionResult, TraceEvent


NodeName = Literal["ANALYZE", "PLAN", "EXECUTE", "TEST", "FIX", "REVIEW"]
Phase = Literal["START", "ANALYZE", "PLAN", "EXECUTE", "TEST", "FIX", "REVIEW"]
NODE_NAMES = ("ANALYZE", "PLAN", "EXECUTE", "TEST", "FIX", "REVIEW")


class WorkflowStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"


class NodeSummary(TypedDict):
    success: bool
    summary: str


class WorkflowState(TypedDict):
    task_id: int
    run_id: str
    thread_id: str
    instruction: str
    phase: Phase
    status: Literal["RUNNING", "COMPLETED", "FAILED", "INTERRUPTED"]
    analysis_result: str | None
    plan: list[str]
    execution_result: NodeSummary | None
    test_result: NodeSummary | None
    review_result: NodeSummary | None
    error: str | None
    iteration: int
    max_iterations: int
    trace: list[dict]
    pending_approval_id: NotRequired[str | None]
    review_decision: NotRequired[str | None]


class NodeOutcome(BaseModel):
    """Trusted handler output. Raw tool data is never copied into checkpoint state."""

    model_config = ConfigDict(extra="forbid", strict=True)
    success: bool = True
    summary: str = Field(max_length=2000)
    plan: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(default_factory=list, max_length=20)
    tool_result: ToolExecutionResult | None = None
    decision: Literal["PASS", "NEEDS_FIX", "FAILED"] | None = None
    fatal_error: str | None = None
    pending_approval_id: str | None = None
    events: list[dict] | None = None


class WorkflowRunResult(BaseModel):
    task_id: int
    run_id: str
    thread_id: str
    status: Literal[WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.INTERRUPTED]
    output: str | None
    error: str | None
    iterations: int
    trace: list[TraceEvent]
    state: WorkflowState
