from dataclasses import dataclass

from app.harness.models import ToolRiskLevel


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 2
    backoff_seconds: float = 0.0

    def __post_init__(self):
        if not 1 <= self.max_attempts <= 5 or not 0 <= self.backoff_seconds <= 30:
            raise ValueError("invalid_retry_policy")

    def retryable(self, error: str | None) -> bool:
        return error in {"cli_timeout", "mcp_call_tool_failed", "process_start_failed", "tool_timeout"}


class PermissionPolicy:
    @staticmethod
    def allows(role: str, risk: ToolRiskLevel) -> bool:
        if risk == ToolRiskLevel.PRIVILEGED:
            return False
        if risk == ToolRiskLevel.WRITE:
            return role == "DEVELOPER"
        if risk == ToolRiskLevel.SAFE_EXECUTION:
            return role in {"DEVELOPER", "TESTER"}
        return role in {"PLANNER", "DEVELOPER", "TESTER", "REVIEWER"}

    @staticmethod
    def requires_approval(risk: ToolRiskLevel) -> bool:
        return risk in {ToolRiskLevel.WRITE, ToolRiskLevel.PRIVILEGED}
