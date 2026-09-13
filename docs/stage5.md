# Stage 5：Roles + Retry / Recovery + Human Approval

本阶段使用固定职责的 Agent 执行现有 LangGraph 节点，增加有限工具重试、风险治理和持久化人工审批。没有自由 Agent 聊天、真实模型或第二套 Workflow Engine。

## 角色与模型边界

`app/agents/roles.py` 定义 AgentRole、AgentContext、AgentResult 与四个 RoleAgent 实现：

| 角色 | Workflow 节点 | 职责 |
| --- | --- | --- |
| PlannerAgent | ANALYZE / PLAN | 分析任务、返回结构化计划 |
| DeveloperAgent | EXECUTE / FIX | 选择允许的工具，执行或修复 |
| TesterAgent | TEST | 调用测试工具，分析测试结果 |
| ReviewerAgent | REVIEW | 检查目标与结果，返回 PASS / NEEDS_FIX / FAILED |

AgentContext 包含 role、task_id、run_id、instruction、workflow_state、available_tools、attempt。角色通过现有 ModelAdapter.generate 接收 HarnessContext；metadata 携带角色、当前节点、尝试次数和有界状态摘要。模型只能返回已有的结构化 HarnessAction。

FINAL.content 必须是可解析为 AgentResult 的 JSON，包含 success、output、requested_tool_calls、decision、error，可附 plan。REVIEW 必须明确给出决策，不能仅请求工具就自动通过。PLAN 必须给出非空计划。TEST 必须实际返回测试工具结果，不能凭文字宣称通过。

为保持 Demo 有界，每个节点最多请求一个工具；可通过 TOOL_CALL 直接请求，或在结构化 AgentResult 中附带请求。生产代码没有默认 Fake 或真实 LLM，测试中使用 ScriptedModel。Agent 不控制节点跳转：REVIEW 的 NEEDS_FIX 由图路由至 FIX，随后 TEST → REVIEW，仍受 max_iterations 限制。

## 统一工具治理与 Retry

`app/governance/runtime.py` 的 ToolRuntime 使用调用方提供的现有 ToolRegistry，依次执行 schema 校验、角色权限判断、审批判断和有限重试。底层执行仍由已有 ToolAdapter 完成，不重复实现 CLI 或 MCP 进程/session 管理。

RetryPolicy 默认 max_attempts=2，允许 1–5 次，计数包括首次执行。只有明确的错误码 tool_timeout、cli_timeout、mcp_call_tool_failed、process_start_failed 可重试。schema 错误、workspace_escape、permission_denied、unknown_tool、invalid action 和未知异常均不重试。

backoff_seconds 默认 0，可注入异步 sleeper；测试用记录函数代替真实等待。Trace 的 TOOL_CALLED / RETRY_STARTED 记录 attempt；用尽次数返回 retry_exhausted 并产生 RETRY_EXHAUSTED。写操作批准后仅执行一次，不应用自动重试。

实际测试失败（test_failed / nonzero_exit_code）走 Workflow FIX 分支；权限、参数等错误和重试耗尽进入 FAILED。Agent 非结构化/无效结果明确失败，不重新调用模型掩盖问题。AgentContext.attempt 是模型尝试次数，本阶段不重试模型，因此为 1；工具 attempt 由 Runtime 单独计数。

## 风险与权限

ToolDefinition 增加 risk_level：

| 等级 | 当前工具 | 规则 |
| --- | --- | --- |
| READ_ONLY | git_status、git_diff、search_text、两个 workspace MCP 工具 | 角色可直接使用 |
| SAFE_EXECUTION | run_pytest | Developer / Tester 可直接使用 |
| WRITE | write_text_file | 仅 Developer 可请求，必须人工审批 |
| PRIVILEGED | 当前不提供 | 默认拒绝 |

为兼容既有工具，未指定风险的定义仍默认 READ_ONLY；注册代码是可信配置，新增工具必须正确声明风险。ToolRegistry 对 WRITE / PRIVILEGED 返回 ApprovalGuard，直接 `get(...).execute(...)` 只会得到 permission_denied，旧 HarnessRunner 也不能绕过审批执行已注册的 WRITE 工具。原始写 Adapter 仅由可信 ApprovalService 持有并在批准校验后执行。

## write_text_file

`app/tools/write.py` 接收相对 path 和 UTF-8 content；父目录必须已存在，不创建目录。使用 WorkspacePolicy 校验父路径与已存在目标，拒绝绝对路径、驱动器路径、..、目录逃逸、符号链接/目录联接目标及多硬链接文件。

拒绝 .env*、.git、常见密钥路径和数据库/审批密钥文件；限制 WRITE_MAX_CHARS，拒绝 NUL 与不可编码文本。不提供删除、rename、chmod、commit、push、reset、shell 或 Docker 操作。共享 WorkspacePolicy 同时阻止 CLI / MCP 读取 `.approval-key` 文件。

## PendingApproval 持久化与审计

使用现有 SQLAlchemy 数据库中的独立 approvals 表，包含 approval_id、task_id、run_id、tool_name、risk_level、status、arguments_summary、requested_by、decided_by、decision、result 以及创建/决策/完成时间。时间沿用项目 UTC 约定。

状态流：PENDING → APPROVED → EXECUTED / FAILED，或 PENDING → REJECTED。requested_by 记录 Agent 角色，当前 decided_by 固定为 human。审批是独立治理记录，LangGraph checkpoint 仅保存 pending_approval_id，不作为唯一审批数据来源。

API 展示路径、字符数和内容 SHA-256，不展示文件正文。实际请求以 Fernet 认证加密后保存在 encrypted_payload，包括固定的参数、workspace、解析目标和风险信息；结束后清除载荷，仅保留审计记录。密钥由配置 APPROVAL_ENCRYPTION_KEY 提供，通过 pydantic-settings 从环境变量或被忽略的 .env 读取，类型为 SecretStr；API 和 Trace 不返回密钥或密文。

没有默认密钥，不生成或读取本地密钥文件。未配置或格式不合法时拒绝创建加密审批请求。.env.example 只有无效占位符，使用前必须配置自己的有效 Fernet 密钥。数据库必须是文件 SQLite；未完成审批恢复时须使用相同密钥。测试夹具每次动态生成临时随机密钥并通过 Settings 注入，不包含固定密钥。既有本地 .approval-key 文件不会自动迁移或提交，其忽略及读取保护规则继续保留。

## Approval API

| 接口 | 行为 |
| --- | --- |
| GET /api/approvals/{approval_id} | 查看脱敏审批与审计信息；不存在返回 404 |
| POST /api/approvals/{approval_id}/approve | 仅接收 PENDING 请求，执行保存的受控写操作 |
| POST /api/approvals/{approval_id}/reject | PENDING → REJECTED，不执行工具 |

approve / reject 不接受替换工具或写入参数。使用数据库条件更新原子占用 PENDING，重复 approve / reject 返回 409。批准前重新检查工具名、WRITE 风险、workspace、目标路径和最新输入限额，校验失败标为 FAILED。

API 负责决策和工具执行，随后由持有模型/角色配置的调用方显式调用 WorkflowRunner.resume(thread_id)，不在 API 中偷偷创建模型或重新从 START 执行。当前无认证系统，只适用于可信本地环境。

## interrupt → approval → resume

AgentNodeHandler 将工具请求交给 ToolRuntime。WRITE 请求校验通过后创建 PendingApproval，不执行写入，节点仅保存审批 ID；图进入专用 APPROVAL 等待节点，使用[LangGraph 官方 interrupt](https://docs.langchain.com/oss/python/langgraph/interrupts) 暂停。

approve API 执行写操作并保存 EXECUTED / FAILED；reject 保存拒绝结果。WorkflowRunner.resume 先校验审批属于相同 task/run 且已经决策，再以 Command(resume=True) 恢复官方 checkpoint。尚未解决的审批不能恢复。

恢复只重入无副作用的 APPROVAL 等待节点，读取已保存结果，不重跑原 Agent 或写操作。成功后继续 TEST / REVIEW；拒绝或执行失败成为工具失败并终止 Workflow。新的 Runner、Handler 和 ApprovalService 可以使用相同数据库与 thread_id 恢复。

构建入口示意（model_adapters 由调用方提供）：

```python
from app.agents.handler import AgentNodeHandler
from app.agents.roles import PlannerAgent, DeveloperAgent, TesterAgent, ReviewerAgent
from app.governance.approval import ApprovalService
from app.governance.policy import RetryPolicy
from app.governance.runtime import ToolRuntime
from app.tools.write import WriteTextFile
from app.tools.workspace import WorkspacePolicy
from app.workflow.runner import WorkflowRunner

writer = WriteTextFile(WorkspacePolicy(settings.workspace_root), settings.write_max_chars)
tools.register(writer)  # tools 是现有 ToolRegistry，可已有 CLI / MCP 工具
approvals = ApprovalService(engine, writer, settings.approval_encryption_key)  # engine 的 Base.metadata 已完成建表
handler = AgentNodeHandler(
    PlannerAgent(planner_model), DeveloperAgent(developer_model),
    TesterAgent(tester_model), ReviewerAgent(reviewer_model),
    ToolRuntime(approvals, RetryPolicy(settings.retry_max_attempts)),
)
runner = WorkflowRunner(handler, tools, settings)
```

## Trace、配置与验证

复用 TraceCollector，新增 AGENT_STARTED / COMPLETED、RETRY_STARTED / EXHAUSTED、APPROVAL_REQUIRED / APPROVED / REJECTED、PERMISSION_DENIED。摘要固定且简短，不保存文件正文、工具参数、大输出、环境变量或 traceback。批准/拒绝事件在恢复时并入 workflow trace；即使暂不恢复，独立审批表已保存决策和执行审计。

新增 RETRY_MAX_ATTEMPTS=2、WRITE_MAX_CHARS=20000、APPROVAL_ENCRYPTION_KEY；.env.example 的密钥仅为占位符。

```powershell
pytest tests/test_stage5.py
```

Stage 5 测试涵盖角色职责链、Reviewer 分支、重试分类、审批 API、写前文件状态、风险路径及跨 Runner 恢复。审批前断言文件不存在，批准后断言内容正确，拒绝后仍不存在；模型调用列表证明恢复只执行剩余节点。另针对受影响的 Stage 4 静态 interrupt/resume 行为做少量回归，不运行前面阶段全量测试。

## 当前边界

同一 thread 应串行恢复。批准状态原子占用可阻止普通重复调用，但进程若在写入后、保存结果前退出，可能保留 APPROVED；需要人工检查，不自动重复执行，不保证 exactly-once 外部副作用。

WorkspacePolicy 无法消除恶意本地进程并发替换文件的所有竞态；可信 Python 代码仍可绕过应用层接口。pytest 也会执行仓库代码，重试可能重复其副作用。本阶段不是操作系统沙箱，也不提供复杂补偿事务或自动 git push。

没有前端、真实 Qwen / DashScope、Docker、任意自主 shell 或大型分布式调度。
