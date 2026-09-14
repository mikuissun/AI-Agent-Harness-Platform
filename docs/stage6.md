# Stage 6

## Frontend Runtime Console

`frontend/` 提供 AI Agent Harness Platform / Agent Runtime Console。使用 Vue 3、TypeScript、Vite、Axios 和原生 CSS；单页不引入 Router、Pinia、UI 框架或流程图库。README 留待最终联调后统一整理。

页面采用三栏：深色 Tasks / Runs 侧栏、中性 Run Workspace、深色 Execution Trace 时间线。支持任务创建和选择、启动运行、刷新、按 Run ID 打开运行、状态 Badge、Workflow Stepper、结果和 token 用量。ANALYZE → PLAN → EXECUTE → TEST → REVIEW 根据真实 Trace 标记阶段；FIX 分支、Approval Required 和 Checkpoint Resume 单独展示。Trace 可按 Agent、Tool、Governance、Workflow、Retry 分类筛选，工具显示 CLI / MCP / WRITE 标签。

Human Approval Card 仅在 INTERRUPTED 且存在审批记录时显示，展示风险、路径、字符数、SHA-256 和审批状态，不读取完整写入正文。Approve / Reject → 刷新 Approval → 使用原 run_id 调用 Resume → 更新 Run / Trace。整个操作期间锁定按钮，并在函数入口防重复提交。决策响应失败时只刷新记录，不自动重发决策；已完成决策可通过 Resume workflow 继续。

API 统一封装在 `src/api/{client,tasks,runs,approvals}.ts`。Vite 开发服务器将 `/api` 代理到 `http://127.0.0.1:8000`；后端无需增加 CORS 或修改 API。运行页仅显示有限摘要，不渲染 HTML、原始 Tool arguments、stdout 或 traceback。生产页面没有 mock Trace，不读取 `.demo-runs/`。该目录及 SQLite、临时副本、报告仍由 Git 忽略。

### 现有 API 边界

- POST Run 是等待式请求：结束或 INTERRUPTED 才返回 ID 和 Trace。前端显示等待响应，不伪造实时 phase 或 trace。若后端返回 RUNNING 且已有 ID，每 2 秒查询一次，终态或 INTERRUPTED 停止。
- 后端没有 Run 列表 API。浏览器仅保存已知的 task_id → run_id 映射，重新打开时从后端查询最新数据。侧栏明确标注 “No known run”，不把 Task 的 PENDING 当成 Run 状态；其他浏览器的运行可通过 ID 打开。不会把 token、任务正文、Trace 或审批载荷写入 localStorage。
- 网络中断可能发生在后端已经接受操作之后。前端不自动重试 POST；无 ID 的运行无法靠现有 API 自动找回。本次没有新增后台任务、Run 列表或实时通道。
- Runtime Ready 表示 `/api/info` 可访问，不表示已经探测模型凭据或调用过 Qwen。生产部署需由同源反向代理提供 `/api`，Vite proxy 仅用于开发。

### 本地使用

先按现有方式启动 FastAPI；在另一个终端：

```powershell
cd frontend
npm ci
npm run dev
```

默认前端地址 `http://127.0.0.1:5173`。启动运行、批准及恢复会触发真实后端操作；本次 UI 验证不触发这些真实调用。

### 最小验证

```powershell
cd frontend
npm test
npm run build
```

前端测试使用 Playwright 和本机 Chrome headless，在浏览器侧拦截所有 API，覆盖创建、运行等待与完成、Trace 分类、审批卡片、Approve / Reject 防重复和原 Run Resume。两种桌面宽度 1280 / 1440 的布局溢出断言包含 Completed 与 Interrupted 页面。测试仅存在于 `frontend/tests/`，不启动后端、不运行 pytest、不调用模型、不生成模拟截图。构建先执行 `vue-tsc --noEmit`，再生成 `frontend/dist/`；node_modules、dist、测试产物均忽略。

`runtime-overview.png`、`human-approval.png`、`checkpoint-resume.png` 留待真实 UI 联调后截图，本次不生成虚假截图。

本次验证：6 个前端测试通过（先修复测试拦截规则误匹配 Vite 源码路径的问题，再仅重跑失败项）；1280 / 1440 的 Completed 与 Interrupted 布局无横向溢出。正式 build 首次通过，主 JS 138.05 kB（gzip 52.10 kB）、CSS 16.54 kB（gzip 4.47 kB）。未运行任何后端测试或真实 Qwen E2E；尚未进行真实前后端 UI 联调和截图。

实现参考：[Vue TypeScript](https://vuejs.org/guide/typescript/overview.html)、[Vite proxy](https://vite.dev/config/server-options#server-proxy)、[Playwright API mocking](https://playwright.dev/docs/mock)。
