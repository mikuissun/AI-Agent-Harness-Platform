from app.agents.roles import AgentContext, DeveloperAgent, PlannerAgent, ReviewerAgent, TesterAgent, available_for
from app.governance.runtime import ToolRuntime
from app.harness.models import TraceEventType
from app.workflow.nodes import restore_trace, trace_data
from app.workflow.state import NodeOutcome


class AgentNodeHandler:
    def __init__(self, planner: PlannerAgent, developer: DeveloperAgent, tester: TesterAgent, reviewer: ReviewerAgent, runtime: ToolRuntime):
        self.agents = {"ANALYZE": planner, "PLAN": planner, "EXECUTE": developer, "FIX": developer, "TEST": tester, "REVIEW": reviewer}
        self.runtime = runtime

    async def handle(self, node, state, tools):
        agent = self.agents[node]
        trace = restore_trace(state)
        trace.record(state["iteration"], TraceEventType.AGENT_STARTED, agent.role.value, True, "Agent started")
        try:
            result = await agent.run(AgentContext(
                role=agent.role, task_id=state["task_id"], run_id=state["run_id"],
                instruction=state["instruction"], workflow_state=state,
                available_tools=available_for(agent.role, tools.definitions()),
            ))
        except Exception:
            trace.record(state["iteration"], TraceEventType.AGENT_COMPLETED, agent.role.value, False, "Invalid agent result")
            return NodeOutcome(success=False, summary="Agent failed", fatal_error="invalid_agent_result", events=trace_data(trace))
        trace.record(state["iteration"], TraceEventType.AGENT_COMPLETED, agent.role.value, result.success, "Agent completed")
        outcome = NodeOutcome(success=result.success, summary=result.output, plan=result.plan,
                              decision=result.decision.value if result.decision else None,
                              fatal_error="agent_failed" if not result.success else None)
        if result.success and result.requested_tool_calls:
            governed = await self.runtime.call(tools, result.requested_tool_calls[0], state, agent.role.value, trace)
            outcome.pending_approval_id = governed.approval_id
            outcome.tool_result = governed.result
            if governed.result and not governed.result.success and governed.result.error not in {"nonzero_exit_code", "test_failed"}:
                outcome.fatal_error = "tool_failed"
        outcome.events = trace_data(trace)
        return outcome

    def check_approval(self, state):
        return self.runtime.approvals.resolved_result(state["pending_approval_id"], state["task_id"], state["run_id"])

    async def resume_approval(self, state):
        result = self.check_approval(state)
        record = self.runtime.approvals.get(state["pending_approval_id"])
        trace = restore_trace(state)
        trace.record(state["iteration"], TraceEventType.WORKFLOW_RESUMED, "workflow", True, "Approval resolved; workflow resumed")
        event = TraceEventType.APPROVAL_REJECTED if record["decision"] == "REJECT" else TraceEventType.APPROVAL_APPROVED
        trace.record(state["iteration"], event, record["tool_name"], result.success, "Approval rejected" if record["decision"] == "REJECT" else "Approval granted")
        return NodeOutcome(summary="Approved operation completed" if result.success else "Approved operation denied or failed",
                           success=result.success, tool_result=result, fatal_error=None if result.success else "approval_failed", events=trace_data(trace))
