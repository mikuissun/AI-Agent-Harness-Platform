import { test, expect, type Page } from '@playwright/test'
import type { Task, Run, Approval, TraceEvent } from '../src/api/types'

// Fixtures are test-only. Every /api request is intercepted; no backend or model is contacted.
const task: Task = { id: 1, title: 'Repair calculator behavior', instruction: 'Inspect and repair calculator.py. Run the relevant tests after approval.', status: 'PENDING', created_at: '2026-09-13T04:00:00' }
function event(sequence: number, event_type: string, name: string, success = true): TraceEvent {
  return { sequence, event_type, name, success, iteration: 1, summary: `${name} ${success ? 'recorded' : 'denied'}` }
}
const baseTrace = [event(1,'WORKFLOW_STARTED','workflow'), event(2,'NODE_STARTED','ANALYZE'), event(3,'AGENT_STARTED','PLANNER'), event(4,'TOOL_CALLED','mcp.workspace_read_text'), event(5,'NODE_COMPLETED','ANALYZE'), event(6,'NODE_STARTED','PLAN'), event(7,'NODE_COMPLETED','PLAN'), event(8,'NODE_STARTED','EXECUTE'), event(9,'AGENT_STARTED','DEVELOPER')]
function makeRun(status: Run['status']): Run {
  return { run_id: 'run-test-123456789', thread_id: 'thread-test-123456789', task_id: 1, status, error: status === 'FAILED' ? 'approval_failed' : null,
    output: status === 'COMPLETED' ? 'The requested change is verified. Selected tests passed.' : null,
    pending_approval_id: status === 'INTERRUPTED' ? 'approval-test' : null,
    usage: { calls: 9, prompt_tokens: 6161, completion_tokens: 334 },
    trace: [...baseTrace, ...(status === 'INTERRUPTED' ? [event(10,'APPROVAL_REQUIRED','write_text_file'), event(11,'WORKFLOW_INTERRUPTED','workflow')] : [event(10,'NODE_COMPLETED','EXECUTE'),event(11,'NODE_STARTED','TEST'),event(12,'AGENT_STARTED','TESTER'),event(13,'TOOL_CALLED','run_pytest'),event(14,'NODE_COMPLETED','TEST'),event(15,'NODE_STARTED','REVIEW'),event(16,'AGENT_STARTED','REVIEWER'),event(17,'NODE_COMPLETED','REVIEW'),event(18,'WORKFLOW_COMPLETED','workflow')])] }
}
const approval: Approval = { approval_id: 'approval-test', run_id: 'run-test-123456789', task_id: 1, tool_name: 'write_text_file', risk_level: 'WRITE', status: 'PENDING', arguments_summary: {path: 'calculator.py', characters: 52, sha256: 'a'.repeat(64)} }
async function setup(page: Page, options: { empty?: boolean; status?: Run['status']; delayedRun?: boolean; delayedDecision?: boolean; usage?: Run['usage']; approved?: boolean } = {}) {
  let tasks = options.empty ? [] : [task], run = makeRun(options.status ?? 'COMPLETED'), item = structuredClone(approval)
  if (options.usage) run.usage = options.usage
  if (options.approved) run.trace.push(event(19,'APPROVAL_REQUIRED','write_text_file'), event(20,'APPROVAL_APPROVED','write_text_file'), event(21,'WORKFLOW_RESUMED','workflow'))
  const requests: string[] = []
  let release: (() => void) | undefined
  const gate = new Promise<void>(resolve => { release = resolve })
  if (options.status) await page.addInitScript(() => localStorage.setItem('harness.console.run-ids.v1', JSON.stringify({1:'run-test-123456789'})))
  await page.route('http://127.0.0.1:5179/api/**', async route => {
    const method = route.request().method(), path = new URL(route.request().url()).pathname
    requests.push(`${method} ${path}`)
    let data: unknown
    if (path === '/api/info') data = {project_name: 'Harness', version: '0.1.0', environment: 'local'}
    else if (path === '/api/tasks' && method === 'GET') data = tasks
    else if (path === '/api/tasks' && method === 'POST') { const input = route.request().postDataJSON(); tasks = [{...task, ...input}]; data = tasks[0] }
    else if (path === '/api/tasks/1/run') { if (options.delayedRun) await gate; data = run }
    else if (path === `/api/runs/${run.run_id}/resume`) {
      run = makeRun(item.status === 'REJECTED' ? 'FAILED' : 'COMPLETED'); run.trace.push(event(19,'WORKFLOW_RESUMED','workflow'),event(20,item.status === 'REJECTED' ? 'APPROVAL_REJECTED' : 'APPROVAL_APPROVED','write_text_file',item.status !== 'REJECTED')); data = run
    }
    else if (path === `/api/runs/${run.run_id}`) data = run
    else if (path === '/api/approvals/approval-test') data = item
    else if (/\/api\/approvals\/approval-test\/(approve|reject)$/.test(path)) {
      if (options.delayedDecision) await gate
      item = {...item, status: path.endsWith('/reject') ? 'REJECTED' : 'EXECUTED'}; data = item
    } else { await route.fulfill({status:404, json:{detail:'fixture_not_found'}}); return }
    await route.fulfill({json:data})
  })
  await page.goto('/')
  return { requests, release: () => release?.() }
}
async function noOverflow(page: Page) {
  for (const width of [1280,1440]) {
    await page.setViewportSize({width,height:1000})
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    for (const selector of ['.workspace','.sidebar','.trace-panel']) expect(await page.locator(selector).evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true)
  }
}

test('creates and selects a real API task without auto-starting a run', async ({page}) => {
  const {requests} = await setup(page,{empty:true})
  await expect(page.getByText('创建任务以启动一个可追踪的 Agent Workflow。')).toBeVisible()
  await expect(page.getByText('智能体运行控制台',{exact:true})).toBeVisible()
  await noOverflow(page)
  await page.getByRole('complementary',{name:'任务与运行'}).getByRole('button',{name:'新建任务',exact:false}).click()
  await page.getByLabel('标题',{exact:true}).fill('Inspect workspace')
  await page.getByLabel('任务指令',{exact:true}).fill('Read the relevant source files.')
  await page.getByRole('button',{name:'创建任务',exact:true}).click()
  await expect(page.getByRole('heading',{name:'Inspect workspace'})).toBeVisible()
  expect(requests.filter(r => r === 'POST /api/tasks')).toHaveLength(1)
  expect(requests.some(r => r.endsWith('/run'))).toBe(false)
})
test('shows synchronous run progress then completed status at both desktop widths', async ({page}) => {
  const {requests,release} = await setup(page,{delayedRun:true})
  await page.getByRole('button',{name:'启动运行'}).click()
  await expect(page.getByText('等待响应',{exact:true})).toBeVisible()
  await expect(page.getByRole('button',{name:'启动运行'})).toBeDisabled()
  release()
  await expect(page.getByRole('region',{name:'运行状态'})).toContainText('COMPLETED')
  await expect(page.getByRole('region',{name:'最终结果'})).toContainText('Selected tests passed.')
  await noOverflow(page)
  expect(requests.filter(r => r === 'POST /api/tasks/1/run')).toHaveLength(1)
})
test('renders categorized trace, CLI/MCP labels and filters', async ({page}) => {
  await setup(page,{status:'COMPLETED'})
  const trace = page.getByRole('complementary',{name:'执行轨迹'})
  await expect(trace.getByRole('heading',{name:'PLANNER',exact:true})).toBeVisible()
  await expect(trace.getByRole('heading',{name:'REVIEWER',exact:true})).toBeVisible()
  await trace.getByRole('button',{name:'工具',exact:true}).click()
  await expect(trace.getByRole('heading',{name:'MCP workspace_read_text'})).toBeVisible()
  await expect(trace.getByRole('heading',{name:'CLI run_pytest'})).toBeVisible()
  await expect(trace.getByRole('heading',{name:'PLANNER',exact:true})).toHaveCount(0)
})
test('interrupted run shows sanitized approval and checkpoint state without overflow', async ({page}) => {
  await setup(page,{status:'INTERRUPTED'})
  const card = page.getByRole('region',{name:'人工审批'})
  await expect(card).toContainText('calculator.py')
  await expect(card).toContainText('PENDING')
  await expect(card).toContainText('52 个字符')
  await expect(page.getByRole('region',{name:'工作流'})).toContainText('Checkpoint / Resume')
  await expect(page.getByRole('button',{name:'启动运行'})).toBeDisabled()
  await noOverflow(page)
})
for (const decision of ['approve','reject'] as const) test(`${decision} prevents duplicate clicks and resumes the same run`, async ({page}) => {
  const {requests,release} = await setup(page,{status:'INTERRUPTED',delayedDecision:true})
  const button = page.getByRole('button',{name:decision === 'approve' ? '批准并 Resume' : '拒绝',exact:true})
  await button.click()
  await expect(page.getByRole('button',{name:'拒绝',exact:true})).toBeDisabled()
  await expect(page.getByRole('button',{name:'批准并 Resume',exact:true})).toBeDisabled()
  await button.dispatchEvent('click')
  expect(requests.filter(r => r.endsWith(`/${decision}`))).toHaveLength(1)
  release()
  await expect(page.getByRole('region',{name:'运行状态'})).toContainText(decision === 'approve' ? 'COMPLETED' : 'FAILED')
  expect(requests.filter(r => r === 'POST /api/runs/run-test-123456789/resume')).toHaveLength(1)
  expect(requests.filter(r => r === 'POST /api/tasks/1/run')).toHaveLength(0)
  expect(requests.filter(r => r === 'GET /api/approvals/approval-test').length).toBeGreaterThanOrEqual(2)
})

for (const status of ['COMPLETED', 'INTERRUPTED', 'FAILED'] as const) test(`task card shows latest Run status clearly: ${status}`, async ({page}) => {
  await setup(page, {status})
  const card = page.getByRole('navigation', {name:'任务列表'}).getByRole('button')
  await expect(card).toContainText(`Run · ${status}`)
  await expect(card).not.toContainText('PENDING')
})

test('completed approval run hides waiting hint', async ({page}) => {
  await setup(page, {status:'COMPLETED', approved:true})
  await expect(page.getByRole('region', {name:'运行状态'})).toContainText('COMPLETED')
  await expect(page.getByRole('region', {name:'工作流'})).not.toContainText('等待人工审批')
})
for (const [input, output] of [[0,0], [120,0], [0,30]]) test(`token footer respects usage ${input}/${output}`, async ({page}) => {
  await setup(page, {status:'COMPLETED', usage:{calls:3, prompt_tokens:input!, completion_tokens:output!}})
  await expect(page.getByRole('region', {name:'运行状态'})).toContainText('COMPLETED')
  const footer = page.locator('.token-footer')
  if (input === 0 && output === 0) await expect(footer).toHaveCount(0)
  else {
    await expect(footer).toContainText(`${input} 输入 tokens`)
    await expect(footer).toContainText(`${output} 输出 tokens`)
  }
})
