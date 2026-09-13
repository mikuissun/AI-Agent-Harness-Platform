# Stage 3：Controlled CLI Adapter + MCP Client / Server

HarnessRunner 通过现有 ToolRegistry / ToolAdapter 使用 CLI 和 MCP，Runner、Action、Trace 和统一结果接口保持不变。本阶段不新增 FastAPI 业务接口。

## CLI Adapter

`app/tools/cli.py` 中的 CliToolSpec 保存名称、描述、executable、base_args、由 Pydantic 参数模型生成的 input_schema 和 timeout_seconds。`create_cli_tools()` 只创建以下四个工具；CliToolAdapter 校验结构化参数后组装 argv，不接受 command 或额外命令行选项。

| 工具 | 参数 | 行为 |
| --- | --- | --- |
| git_status | 无 | `git status --short`，范围限定当前 workspace |
| git_diff | staged，默认 false | `git diff`；staged=true 加 `--cached` |
| run_pytest | paths，1–20 个路径 | 当前 Python 运行指定测试文件或目录；文件名须为 test_*.py |
| search_text | text；path 默认 . | rg 按字面文本搜索；退出码 1 视为正常无匹配 |

不提供任意 shell 字符串、`shell=True`、用户指定 executable / base_args、Python 代码、pytest 选项或 npm 脚本接口。命令和参数分离，路径使用绝对路径和 `--` 参数终止符，搜索内容不能变成选项。Windows 仅接受 .exe，避免批处理文件隐式调用 shell。没有 rg 时返回 executable_not_found，不安装工具。

Git 禁用外部 diff、textconv、fsmonitor、分页器和子模块检查，忽略全局及系统配置，diff 排除常见环境文件和密钥扩展名。pytest 清空 addopts，禁用插件自动加载、pytest 缓存和 Python 字节码写入。可信仓库的显式配置和测试代码仍可执行，详见安全边界。

## WorkspacePolicy

`app/tools/workspace.py` 将 root 和调用方路径进行 Path.resolve，再通过 relative_to 判断归属，未使用字符串 startswith。拒绝相对或绝对路径逃逸、指向 workspace 外的符号链接/目录联接、不可用路径、Windows 驱动器相对路径与 NTFS 备用数据流。

工作目录固定为 workspace root。pytest 目录中发现符号链接或目录联接时拒绝运行，避免递归收集跨目录测试。MCP Client 与 Server 都使用同一实现，并各自校验路径。常见敏感路径（.env、.git、.ssh、密钥文件等）禁止直接读取；目录列表跳过这些条目和越界链接。

## timeout 与 output limit

`app/tools/process.py` 是内部进程执行实现，不注册为工具。Popen 始终使用 shell=False，stdin 关闭，stdout/stderr 分别捕获，环境只传递必要系统变量和固定运行配置，不继承 API Key、密码或完整环境。

Windows 子进程以隐藏、挂起状态创建，加入启用 KILL_ON_JOB_CLOSE 的 Job Object 后才恢复，清理时结束整个 Job。POSIX 创建独立进程组并在清理时发送 SIGKILL。超时和外层取消均清理并等待进程退出；正常完成也清理残留后代。调用取消在清理后继续向上层传播。

两个流持续分块读取并共用 CLI_MAX_OUTPUT_CHARS 字符预算，超出部分丢弃但继续排空管道，避免堵塞和无限缓冲。结果 data 包含 exit_code、stdout、stderr、truncated；超时返回 cli_timeout，非零退出返回 nonzero_exit_code。输出截断本身不将成功命令标记为失败。

Trace 沿用 Stage 2 的固定摘要，不保存 stdout/stderr、工具数据或 traceback。

## MCP Server

使用[官方 MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x) 的 1.x API，依赖限定 `mcp>=1.28,<2.0`。协议、初始化和 stdio 传输由 SDK 实现。

`app/tools/mcp_server.py` 只提供：

- workspace_list_files：列出指定目录的直接条目，最多 MCP_MAX_LIST_FILES 个，返回 files 和 truncated；不递归。
- workspace_read_text：读取普通 UTF-8 文本文件的有限前缀，最多 MCP_MAX_READ_CHARS 个字符，返回 text 和 truncated。无效 UTF-8 或读取前缀中含二进制控制字符时明确失败；不可读文件返回 file_unreadable。

文件结果限制在服务端生效，调用参数不能放大上限。服务端不加载 .env，不提供写入、删除、shell、HTTP 或 SSE。stdout 专供协议，Client 丢弃服务端 stderr，避免把诊断信息带入 Trace。

## MCP Client、Adapter 与 Registry

McpClient 只启动项目自带的本地服务端，负责 SDK stdio transport、ClientSession、initialize、list_tools、call_tool 和 close。使用 async with 管理资源，进入和关闭必须在同一个异步任务中；调用工具可以来自 HarnessRunner 的子任务。请求使用有限 read timeout，stdio 子进程由 SDK 清理。

启动、初始化、发现、调用和清理错误分别转换为稳定 HarnessError / ToolExecutionResult 错误码，不透传异常文本。未知工具拒绝调用，服务端结构化结果转换为统一 ToolExecutionResult。

McpToolAdapter 将发现的 name、description、inputSchema 转为 ToolDefinition；名称加 `mcp.` 前缀，执行前校验 schema。当前只适配这个本地服务端的结构化结果约定，不自动信任任意第三方服务端。

`app/tools/registry.py` 提供组合入口：

```python
from app.core.config import get_settings
from app.harness.runner import HarnessRunner
from app.tools.registry import workspace_registry

# model 为调用方提供的 ModelAdapter，task 为 HarnessTask。
async def run_task(model, task):
    async with workspace_registry(get_settings()) as registry:
        return await HarnessRunner(model, registry).run(task)
```

Registry 同时包含 git_status、git_diff、run_pytest、search_text、mcp.workspace_list_files 和 mcp.workspace_read_text。所有调用对 Runner 都是 ToolAdapter，不包含 CLI / MCP 判断分支。

## 配置与验证

| 配置 | 默认值 |
| --- | --- |
| WORKSPACE_ROOT | .，按启动进程的工作目录解析 |
| CLI_TIMEOUT_SECONDS | 20，亦用于当前 MCP 请求等待上限 |
| CLI_MAX_OUTPUT_CHARS | 20000，stdout/stderr 共用 |
| MCP_MAX_READ_CHARS | 20000 |
| MCP_MAX_LIST_FILES | 200 |

超时和数量配置必须大于零。HarnessRunner 外层默认调用上限仍为 30 秒；提高工具超时后，如需完整使用该时长，调用方应相应调整 Runner 的 call_timeout_seconds。

```powershell
pytest tests/test_stage3.py
git diff --check
```

测试使用临时 workspace 和测试进程，覆盖 CLI 成功、错误、路径、取消、超时终止、截断，真实 MCP stdio 初始化/发现/调用/清理，以及统一 Registry 和 Harness 集成。不运行既有 Stage 1 / Stage 2 测试或真实模型。

## 当前安全边界与未实现内容

这是可信本地 workspace 的受控开发工具入口，不是操作系统沙箱。pytest 会执行指定测试及相关 conftest / 显式插件，测试本身可能写文件、访问网络或启动进程；不能用于不可信代码。CLI Spec 和可执行程序由可信应用配置，工具参数不能选择命令。

路径检查不能消除恶意进程并发替换文件的检查/使用竞态；POSIX 后代若主动脱离进程组也不属于强隔离保证。已屏蔽常见敏感路径和环境继承，但不能识别任意源文件中硬编码的秘密，git diff / 搜索 / 读取输出仍应按仓库内容管理。

未实现 Qwen / DashScope、LangGraph、Multi-Agent、Checkpoint / Resume、Retry Workflow、Human Approval、文件写入 Agent、前端或 Docker；未提供 commit、push、reset、checkout、删除或任意 shell 工具。
