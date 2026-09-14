import json

from app.agents.roles import AgentContext, DeveloperAgent, PlannerAgent, ReviewerAgent, TesterAgent, available_for, PHASE_TOOL_BUDGETS
from app.governance.runtime import ToolRuntime
from app.harness.models import ExecutionStep, TraceEventType
from app.harness.errors import HarnessError
from app.workflow.nodes import restore_trace, trace_data
from app.workflow.state import NodeOutcome


class AgentNodeHandler:
    def __init__(self, planner: PlannerAgent, developer: DeveloperAgent, tester: TesterAgent, reviewer: ReviewerAgent, runtime: ToolRuntime, tool_feedback: bool = False):
        self.agents = {"ANALYZE": planner, "PLAN": planner, "EXECUTE": developer, "FIX": developer, "TEST": tester, "REVIEW": reviewer}
        self.runtime = runtime
        self.tool_feedback = tool_feedback

    async def handle(self, node, state, tools):
        if self.tool_feedback:
            return await self._with_feedback(node, state, tools)
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

    async def _with_feedback(self, node, state, tools):
        agent = self.agents[node]
        trace = restore_trace(state)
        trace.record(state["iteration"], TraceEventType.AGENT_STARTED, agent.role.value, True, "Agent started")
        history = []
        last_result = None
        outcome = NodeOutcome(success=False, summary="Agent limit reached", fatal_error="agent_step_limit")
        budget = PHASE_TOOL_BUDGETS[node]
        for step in range(budget + 1):
            try:
                result = await agent.run(AgentContext(role=agent.role, task_id=state["task_id"], run_id=state["run_id"],
                    instruction=state["instruction"], workflow_state=state, available_tools=available_for(agent.role, tools.definitions()), tool_feedback=True), history)
            except Exception as exc:
                error = str(exc) if isinstance(exc, HarnessError) and str(exc).startswith("qwen_") else "invalid_agent_result"
                outcome = NodeOutcome(success=False, summary="Model call failed", fatal_error=error)
                break
            if not result.requested_tool_calls:
                summary = result.output
                if node == "ANALYZE":
                    files = []
                    for item in history:
                        if item.action.tool_name == "mcp.workspace_list_files" and isinstance(item.tool_result.data, dict):
                            files.extend(entry.get("path", "") for entry in item.tool_result.data.get("files", []) if isinstance(entry, dict))
                    summary = json.dumps({"workspace_files": ", ".join(files)[:500], "evidence": result.output[:1000]}, ensure_ascii=False)
                outcome = NodeOutcome(success=result.success, summary=result.output, plan=result.plan,
                    decision=result.decision.value if result.decision else None, tool_result=last_result,
                    fatal_error="agent_failed" if not result.success and node != "TEST" else None)
                outcome.summary = summary
                break
            if step >= budget:
                outcome = NodeOutcome(success=False, summary="Tool budget exhausted", fatal_error="role_tool_budget_exceeded")
                break
            call = result.requested_tool_calls[0]
            if any(item.action == call for item in history):
                outcome = NodeOutcome(success=False, summary="Repeated tool request", fatal_error="repeated_tool_request")
                break
            governed = await self.runtime.call(tools, call, state, agent.role.value, trace)
            if governed.approval_id:
                outcome = NodeOutcome(summary="Write awaiting approval", pending_approval_id=governed.approval_id)
                break
            last_result = governed.result
            if not last_result.success and last_result.error not in {"nonzero_exit_code", "test_failed"}:
                outcome = NodeOutcome(success=False, summary="Tool failed", tool_result=last_result, fatal_error="tool_failed")
                break
            history.append(ExecutionStep(action=call, tool_result=last_result))
        trace.record(state["iteration"], TraceEventType.AGENT_COMPLETED, agent.role.value, outcome.success, "Agent completed" if outcome.success else "Agent failed")
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
