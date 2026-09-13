# Stage 4：LangGraph Workflow + Checkpoint / Resume

本阶段用 LangGraph StateGraph 表达软件工程任务的节点和条件边，并使用官方 AsyncSqliteSaver 保存状态和执行位置。WorkflowRunner 与已有 HarnessRunner 并列，未增加 FastAPI endpoint。

## State 与 Handler

`app/workflow/state.py` 定义 WorkflowState，包含 task_id、run_id、thread_id、instruction、phase、status、analysis_result、plan、execution_result、test_result、review_result、error、iteration、max_iterations 和 trace。phase 使用 START / ANALYZE / PLAN / EXECUTE / TEST / FIX / REVIEW Literal。

状态只保存基本类型。节点结果仅保留 success 和最多 2000 字符的摘要；plan 最多 20 项，每项最多 500 字符。原始 ToolExecutionResult、数据库对象、工具参数和 stdout/stderr 不写入 checkpoint。

WorkflowNodeHandler 是异步协议，由调用方提供 `handle(node, state, tools)` 实现：

- ANALYZE 返回任务分析摘要。
- PLAN 必须返回非空计划。
- EXECUTE 执行计划操作，返回摘要和可选工具结果。
- TEST 必须返回测试工具结果；工具失败不能被 Handler 声明为通过。
- FIX 根据 state.test_result 执行修复动作，再交回 TEST。
- REVIEW 汇总结果；成功则完成，否决则失败。

所有 Handler 都是可信应用代码，收到状态深拷贝与现有 ToolRegistry。生产目录只有协议和节点包装，没有 Fake、真实模型或文件写入修复 Agent。测试里的 ScriptedHandler / FakeTool 演示完整流程。

## Node / Edge 与条件路由

```text
START → ANALYZE → PLAN → EXECUTE → TEST
                                  ├─ passed → REVIEW → END
                                  ├─ failed 且未超限 → FIX → TEST
                                  └─ failed 且已达上限 → FAILED → END
```

使用真实 StateGraph 节点和 add_conditional_edges，TEST 根据 test_result.success 选择 FIX 或 REVIEW；其他节点失败直接路由 END。没有自定义循环状态机。节点异常、非法输出、超时或 REVIEW 否决均成为 FAILED，不自动重试。

iteration 表示 TEST 尝试次数。默认 max_iterations=3，允许 1–100：最多执行 3 次 TEST 和 2 次 FIX；最后一次测试通过仍可进入 REVIEW。LangGraph recursion_limit 按持久化迭代上限计算，避免框架默认步数限制提前终止。节点调用默认超时 30 秒，可在 WorkflowRunner 构造时设置。

## ToolRegistry 与 Trace

Handler 使用传入的现有 `ToolRegistry.validate_arguments(name, arguments)` 和 `ToolRegistry.get(name).execute(arguments)` 调用工具。没有第二套 Registry 或 CLI / MCP 执行器，WorkspacePolicy 等边界仍由现有适配器执行。使用 MCP 时，调用方应保持其资源上下文开放，直至 workflow 调用结束。

TraceCollector 新增 from_snapshot，用于恢复旧事件并继续 sequence；原有构造、record 和 snapshot 行为不变。TraceEventType 增加 WORKFLOW_STARTED、NODE_STARTED、NODE_COMPLETED、WORKFLOW_INTERRUPTED、WORKFLOW_RESUMED、WORKFLOW_COMPLETED、WORKFLOW_FAILED。

事件使用固定摘要和节点名称，不记录 Handler 摘要正文、工具输出、敏感参数、环境变量或 traceback。完整 Trace 随状态持久化，恢复后仍从原序号继续；LangSmith 外部 tracing 显式关闭。

## Checkpoint 与标识

`app/workflow/checkpoint.py` 使用官方 `langgraph-checkpoint-sqlite` 的 AsyncSqliteSaver，表结构和序列化由 LangGraph 管理。配置与业务 tasks 数据库独立，不修改任务记录或自行建 checkpoint 表。

| 标识 | 含义 |
| --- | --- |
| task_id | 业务任务 ID，同一任务可以有多次执行 |
| run_id | WorkflowRunner.run 为一次执行生成的 UUID，resume 不改变它 |
| thread_id | LangGraph checkpoint 查询标识，默认等于 run_id，也可单独指定 |

run 拒绝使用已有 checkpoint 的 thread_id，避免覆盖旧执行。每次 run / resume 打开独立 SQLite 连接并在调用结束时关闭；采用同步 checkpoint durability，在执行下一个节点前完成保存。

## Interrupt / Resume

`run(task, thread_id=..., interrupt_after="EXECUTE")` 使用 LangGraph 官方静态节点边界中断，在 EXECUTE 完成后暂停，下一节点保留为 TEST。Runner 使用官方 aupdate_state 写入 INTERRUPTED 和对应 Trace，as_node 指定最后完成节点，保留其后继边。

`resume(thread_id)` 读取同一文件中的持久化状态和 next，恢复原 run_id、任务、迭代预算及 Trace，再以 `graph.ainvoke(None, config)` 继续。没有向 START 重新提交任务，也不需要原 Runner、Handler 或 Registry 实例留在内存。

resume 可同样传入 interrupt_after，在后续节点再次暂停。不存在的 thread 返回 checkpoint_not_found；COMPLETED / FAILED 的 checkpoint 直接返回原结果，不执行节点。WorkflowRunResult 包含统一的 task_id、run_id、status、output、error、iterations、trace，另含 thread_id 和 WorkflowState，终态为 COMPLETED / FAILED / INTERRUPTED。

```python
from app.core.config import get_settings
from app.workflow.runner import WorkflowRunner

async def example(task, handler, tools):
    settings = get_settings()
    first = await WorkflowRunner(handler, tools, settings).run(
        task, interrupt_after="EXECUTE"
    )
    return await WorkflowRunner(handler, tools, settings).resume(first.thread_id)
```

这是测试友好的暂停边界，不是 Human Approval。没有 kill 进程、sleep 或手工修改 SQLite 来模拟中断。

## 配置与测试

- CHECKPOINT_DATABASE_URL 默认 `sqlite:///./workflow-checkpoints.db`，只接受文件 SQLite，不支持内存库。
- WORKFLOW_MAX_ITERATIONS 默认 3；resume 使用 checkpoint 中的值，不受新 Runner 配置改变影响。

```powershell
pytest tests/test_stage4.py
git diff --check
```

测试覆盖正常/修复路由、超限失败、节点错误、Trace、工具复用和 SQLite 状态。分别在 ANALYZE、PLAN、EXECUTE、TEST、FIX 后暂停，随后使用全新的事件循环、Runner、Handler 和 Registry 恢复，断言两次节点调用列表拼接等于预期顺序，且 EXECUTE 工具仅执行一次；同时检查持久化 snapshot.next。

## 当前边界

只支持稳定图版本和相同工具语义下的节点边界恢复。同一 thread_id 应由单一调用方串行操作，未提供并发所有权或跨进程锁。普通节点异常为终态失败，不提供自动 Retry / Recovery；意外进程退出、节点执行一半后的外部副作用一致性和 exactly-once 语义不在本阶段保证范围。

checkpoint 包含任务指令、计划和 Handler 摘要，应存放在可信本地目录；摘要长度限制不等于秘密检测，Handler 不应将凭据写入摘要。SQLite 文件已受现有 .gitignore 数据库规则保护。

未实现 Multi-Agent、Human Approval、Qwen / DashScope、自动 Retry Policy、复杂 Recovery、前端或 Docker。

官方参考：[LangGraph 持久化](https://docs.langchain.com/oss/python/langgraph/persistence)、[中断与恢复](https://docs.langchain.com/oss/python/langgraph/interrupts)。
