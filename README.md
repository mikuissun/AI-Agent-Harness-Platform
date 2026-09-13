# AI Agent Harness Platform

软件工程任务执行与智能体运行平台。目前完成 Stage 1–4：基础骨架、Harness Core、受控 CLI / MCP 工具，以及 LangGraph Workflow + Checkpoint / Resume。任务 API 只保存记录，不接真实模型。最新设计与边界见 [Stage 4 文档](docs/stage4.md)。

## 当前技术栈

Python 3.12、FastAPI、Pydantic v2、pydantic-settings、SQLAlchemy 2、SQLite、uvicorn；使用 pytest 和 httpx 测试。所有依赖统一维护在 `pyproject.toml`。

## Stage 1 已完成内容

- `GET /health`：返回 `{"status": "ok"}`。
- `GET /api/info`：返回 `project_name`、`version`、`environment`，不暴露敏感配置。
- `POST /api/tasks`：创建任务，返回 201，初始状态为 `PENDING`。
- `GET /api/tasks`：按 ID 升序列出任务。
- `GET /api/tasks/{id}`：读取任务，不存在时返回 404。
- Harness 数据结构 `HarnessTask`、`HarnessState`、`HarnessRunResult`；模型和工具适配协议 `ModelAdapter`、`ToolAdapter`，均无执行实现。
- SQLite `tasks` 表包含 `id`、`title`、`instruction`、`status`、`created_at`、`updated_at`。状态为 `PENDING`、`RUNNING`、`COMPLETED`、`FAILED`；本阶段不推进状态。时间统一为 UTC，SQLite 存储不含时区的时间值。
- API → Service → Database 分层；启动时自动建表。

创建任务请求示例：

```json
{
  "title": "Fix failing tests",
  "instruction": "Analyze and fix the failing pytest tests."
}
```

## 项目结构

```text
app/
  main.py
  api/routes/tasks.py
  core/{config,exceptions}.py
  harness/{models,interfaces,errors,registry,runner,trace}.py
  db/{base,session}.py
  models/task.py
  schemas/task.py
  services/tasks.py
tests/
  conftest.py
  test_api.py
  test_harness.py
docs/
  stage2.md
scripts/dev.ps1
.env.example
.gitignore
pyproject.toml
README.md
```

## 本地启动

在项目根目录使用 Python 3.12（以下为 PowerShell）：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
python -m uvicorn app.main:app --reload
```

也可在激活虚拟环境后运行 `./scripts/dev.ps1`。默认地址为 `http://127.0.0.1:8000`，交互式 API 文档位于 `/docs`，默认数据库为项目根目录的 `tasks.db`。

配置通过环境变量或 `.env` 加载：`APP_NAME`、`APP_ENV`、`DATABASE_URL`。`DASHSCOPE_API_KEY` 和 `LLM_MODEL` 仅预留，Stage 1 不使用；示例密钥和模型名均为占位符。真实 `.env`、虚拟环境和本地数据库已被 Git 忽略。

## 测试

```powershell
pytest tests/
```

Stage 1 有 5 个 API 测试，每个测试使用独立临时 SQLite 数据库。Stage 2 使用 Scripted/Fake 适配器，不调用外部 API；单独运行 `pytest tests/test_harness.py`。核心上下文、结构化 Action、工具注册、循环限制与 Trace 详见 [Stage 2 文档](docs/stage2.md)。

## Roadmap

- Stage 1：FastAPI + Harness Foundation（已完成）
- Stage 2：Harness Core（已完成）
- Stage 3：CLI + MCP（已完成）
- Stage 4：LangGraph Workflow + Checkpoint（已完成）
- Stage 5：Multi-Agent + Retry + Approval（未实现）
- Stage 6：E2E + UI + README（未实现，当前仅有基础 README）

本阶段未实现 Multi-Agent、自动 Retry / Recovery、Human Approval、真实 Qwen 调用及前端。Workflow 通过 Stage 4 的 Scripted Handler 和持久化恢复测试验证。
