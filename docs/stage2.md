# Stage 2：Agent Harness Core

本阶段提供可通过 Python 调用的异步 HarnessRunner，测试使用 ScriptedModel 和 FakeTool。未增加运行 API，现有任务 API 仍只保存任务。

## Context 与 Action

`HarnessContext` 包含 task_id、run_id（每次运行新建 UUID）、instruction、iteration、max_iterations、metadata。每次模型调用收到上下文和历史的深拷贝。

`HarnessAction` 是 Pydantic 判别联合，以 ActionType 区分：

- `FinalAction(type=FINAL, content=...)`：结束运行。
- `ToolCallAction(type=TOOL_CALL, tool_name=..., arguments=...)`：请求调用工具。

模型适配器必须返回上述模型实例。Runner 会重新校验结构，不解析自由文本或直接接受字典。

## Adapter 与 Registry

`ModelAdapter.generate(context, tools, history)` 异步返回 HarnessAction。tools 为 ToolDefinition 列表；history 是按执行顺序排列的 ExecutionStep（action 和 tool_result），工具失败也会进入历史。

`ToolAdapter.definition` 提供 name、description、input_schema（JSON Schema 2020-12）；`execute(arguments)` 异步返回 ToolExecutionResult，包含 success、data、error。Fake 实现仅在测试中。

`ToolRegistry` 按名称注册、查找和列出定义；重复名称抛 DuplicateToolError，未知名称抛 UnknownToolError，均继承 HarnessError。注册时检查 schema 并保存副本；为禁止隐式外部解析，本阶段拒绝所有 `$ref` / `$dynamicRef`。新增 jsonschema 依赖用于执行前校验参数，非法参数不会进入工具。

## Runner 流程与限制

Task → 创建 Context / Trace → ModelAdapter → 校验 Action → FINAL 返回结果，或经 Registry 查找并校验工具参数 → 执行工具 → 记录结果与 Trace → 再调用模型。

默认 `max_iterations=8`，必须为正整数；一次模型调用算一次 iteration。最后一次若返回 FINAL 则成功；若仍请求工具，执行后返回 max_iterations_exceeded，不会调用第九次模型。

未知工具、无效 Action、参数不合法、模型异常和迭代超限返回 FAILED。普通工具失败、工具异常、无效工具结果及工具超时会作为失败 ToolExecutionResult 回传给下一次模型，Runner 不自动重试。异常文本不透传，只使用固定错误代码。

模型与工具的单次异步调用默认超时 30 秒，可通过 call_timeout_seconds 调整。超时依赖适配器正常让出事件循环和响应取消；注册的 Python 工具属于可信代码，这不是进程隔离或恶意代码沙箱。取消运行会继续向调用方传播。

`HarnessRunResult` 保留 task_id、output、error，新增 run_id、iterations、trace，status 限定为 COMPLETED / FAILED。Runner 不修改数据库任务状态。

## Trace

TraceCollector 为每次运行单独收集事件，sequence 从 1 连续递增，包含 iteration、event_type、name、success、summary。

支持 RUN_STARTED、MODEL_CALLED、TOOL_CALLED、TOOL_COMPLETED、RUN_COMPLETED、RUN_FAILED。MODEL_CALLED 在调用完成或失败时记录；TOOL_CALLED 表示进入执行，TOOL_COMPLETED 表示返回成功或失败结果。

Trace 只使用固定摘要和已注册工具名，不记录任务正文、模型输出、工具参数或结果、配置、API Key、环境变量和异常堆栈。摘要最多 200 字符，name 最多 64 字符。工具结果仅保留在运行内存历史中，供模型继续决策；Trace 不持久化。

## 测试与当前边界

```powershell
pytest tests/test_harness.py
```

覆盖直接 FINAL、单次/连续工具调用、未知/重复工具、失败后继续、迭代限制、Trace 顺序与正文排除，以及无效 Action、参数校验、异常、超时、schema 引用拒绝和运行隔离。

本阶段不接真实 Qwen / DashScope、CLI subprocess、MCP、LangGraph、Multi-Agent、Checkpoint / Resume、Human Approval 或前端。无自动 Retry / Recovery，也不提供 Fake Model 生产 API。
