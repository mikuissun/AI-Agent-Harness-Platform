from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.governance.policy import PermissionPolicy
from app.harness.interfaces import ModelAdapter
from app.harness.models import FinalAction, HarnessContext, ToolCallAction, ToolDefinition


class AgentRole(StrEnum):
    PLANNER = "PLANNER"
    DEVELOPER = "DEVELOPER"
    TESTER = "TESTER"
    REVIEWER = "REVIEWER"


class ReviewDecision(StrEnum):
    PASS = "PASS"
    NEEDS_FIX = "NEEDS_FIX"
    FAILED = "FAILED"


class AgentContext(BaseModel):
    role: AgentRole
    task_id: int
    run_id: str
    instruction: str
    workflow_state: dict
    available_tools: list[ToolDefinition]
    attempt: int = 1


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    success: bool = Field(strict=True)
    output: str = Field(max_length=2000)
    requested_tool_calls: list[ToolCallAction] = Field(default_factory=list, max_length=1)
    decision: ReviewDecision | None = None
    error: str | None = Field(default=None, max_length=200)
    plan: list[str] = Field(default_factory=list, max_length=20)


class RoleAgent:
    role: AgentRole

    def __init__(self, model: ModelAdapter):
        self.model = model

    async def run(self, context: AgentContext) -> AgentResult:
        state = context.workflow_state
        action = await self.model.generate(HarnessContext(
            task_id=context.task_id, run_id=context.run_id, instruction=context.instruction,
            iteration=state["iteration"], max_iterations=state["max_iterations"],
            metadata={"role": self.role.value, "phase": state["phase"], "attempt": context.attempt,
                      "workflow_state": {key: state.get(key) for key in ("analysis_result", "plan", "execution_result", "test_result", "review_result")}},
        ), context.available_tools, [])
        if isinstance(action, ToolCallAction):
            if self.role == AgentRole.REVIEWER:
                raise ValueError("review_decision_required")
            call = ToolCallAction.model_validate(action.model_dump(warnings="error"))
            return AgentResult(success=True, output="Tool operation requested", requested_tool_calls=[call])
        if not isinstance(action, FinalAction):
            raise ValueError("invalid_structured_action")
        result = AgentResult.model_validate_json(action.content)
        if self.role == AgentRole.REVIEWER and result.decision is None:
            raise ValueError("review_decision_required")
        return result


class PlannerAgent(RoleAgent):
    role = AgentRole.PLANNER


class DeveloperAgent(RoleAgent):
    role = AgentRole.DEVELOPER


class TesterAgent(RoleAgent):
    role = AgentRole.TESTER


class ReviewerAgent(RoleAgent):
    role = AgentRole.REVIEWER


def available_for(role: AgentRole, tools: list[ToolDefinition]) -> list[ToolDefinition]:
    return [tool for tool in tools if PermissionPolicy.allows(role.value, tool.risk_level)]
