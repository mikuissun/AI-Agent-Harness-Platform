# Stage 1：FastAPI + Harness Foundation

本阶段建立软件工程任务执行与智能体运行平台的基础骨架：提供 HTTP API、配置管理、任务持久化和 Harness 基础抽象。任务只保存到数据库，不执行 AI。

本文描述 Stage 1 的交付范围；后续新增的运行核心见 [Stage 2：Agent Harness Core](stage2.md)。

## 技术栈与结构

Python 3.12、FastAPI、Pydantic v2、pydantic-settings、SQLAlchemy 2、SQLite、uvicorn；测试使用 pytest 和 httpx。依赖统一维护在 `pyproject.toml`。

代码按 API → Service → Database 分层，不引入 Repository 抽象：

- `app/main.py`：应用入口、生命周期、health 和 info 接口。
- `app/api/routes/tasks.py`：任务路由。
- `app/services/tasks.py`：创建、读取、列出任务。
- `app/models/task.py`、`app/schemas/task.py`：数据库模型与请求/响应结构。
- `app/db/`：SQLAlchemy Base、引擎和 Session。
- `app/core/`：配置与异常。
- `app/harness/`：Harness 数据结构和适配协议。
- `tests/test_api.py`：Stage 1 API 测试。

## 配置

使用 pydantic-settings，从环境变量或 `.env` 加载：

| 配置 | 用途 |
| --- | --- |
| APP_NAME | 项目名称 |
| APP_ENV | 运行环境 |
| DATABASE_URL | SQLite 数据库连接地址 |
| DASHSCOPE_API_KEY | 预留，本阶段不使用 |
| LLM_MODEL | 预留，本阶段不使用 |

`.env.example` 提供示例配置和占位符。真实 `.env`、虚拟环境、Python 缓存及本地数据库由 `.gitignore` 排除。

## FastAPI 接口

| 方法与路径 | 行为 |
| --- | --- |
| GET /health | 返回 `{"status": "ok"}` |
| GET /api/info | 返回 project_name、version、environment，不暴露敏感配置 |
| POST /api/tasks | 创建任务，返回 201，初始状态为 PENDING |
| GET /api/tasks | 按 ID 升序列出任务 |
| GET /api/tasks/{id} | 读取任务；不存在时返回 404 |

创建任务请求：

```json
{
  "title": "Fix failing tests",
  "instruction": "Analyze and fix the failing pytest tests."
}
```

title 和 instruction 去除首尾空白后不能为空，title 最多 255 个字符。本阶段不提供修改、删除或执行任务接口。

## tasks 表

应用启动时自动创建 SQLite 表。

| 字段 | 含义 |
| --- | --- |
| id | 整数主键 |
| title | 任务标题 |
| instruction | 任务指令 |
| status | PENDING、RUNNING、COMPLETED 或 FAILED |
| created_at | 创建时间 |
| updated_at | 更新时间 |

时间统一使用 UTC，SQLite 中存储不含时区的时间值。Stage 1 只创建 PENDING 记录，不推进执行状态。

## Harness 基础抽象

Stage 1 定义了 HarnessTask（任务）、HarnessState（任务状态）、HarnessRunResult（运行结果），以及 ModelAdapter（统一模型调用边界）和 ToolAdapter（未来 CLI / MCP 工具边界）协议。

这一阶段只有数据结构和接口，没有 Runner 或真实适配器。Stage 2 已扩展这些协议和结果结构，当前接口以 Stage 2 文档与代码为准。

## 本地启动与测试

在项目根目录使用 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
python -m uvicorn app.main:app --reload
```

已有虚拟环境时直接激活；已有 `.env` 时保留原配置。也可在激活虚拟环境后运行 `./scripts/dev.ps1`。

Stage 1 对应测试命令：

```powershell
pytest tests/test_api.py
```

共 5 个测试：health、创建任务、读取任务、列出任务、任务不存在。每个测试使用独立临时 SQLite 数据库，通过 TestClient / httpx 访问应用，不调用外部 API。

## 阶段边界

Stage 1 不实现模型调用、工具执行、LangGraph Workflow、MCP、CLI subprocess、Multi-Agent、Checkpoint、Retry / Recovery、Human Approval 或前端。

下一阶段：[Stage 2：Agent Harness Core](stage2.md)，增加 Context、结构化 Action、Tool Registry、Runner、Trace 和循环限制。
