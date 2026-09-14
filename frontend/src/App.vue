<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { useConsole } from './useConsole'
import StatusBadge from './components/StatusBadge.vue'
import WorkflowStepper from './components/WorkflowStepper.vue'
import TracePanel from './components/TracePanel.vue'
import ApprovalCard from './components/ApprovalCard.vue'

const { tasks, selectedId, selected, runs, run, approval, busy, error, connected, environment, loading, refreshing, startingTask, refresh, select, create, start, decide, resume, openRun } = useConsole()
const creating = ref(false), title = ref(''), instruction = ref(''), openId = ref(''), titleInput = ref<HTMLInputElement>()
const phase = computed(() => run.value?.trace.filter(e => e.event_type === 'NODE_STARTED').at(-1)?.name ?? '—')
const trace = computed(() => run.value?.trace ?? [])
async function newTask() { creating.value = true; await nextTick(); titleInput.value?.focus() }
async function submit() { if (await create(title.value, instruction.value)) { creating.value = false; title.value = ''; instruction.value = '' } }
function date(value?: string) { if (!value) return ''; const parsed = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : value + 'Z'); return parsed.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' }) }
function short(value: string) { return value.length > 20 ? value.slice(0, 8) + '…' + value.slice(-6) : value }
</script>
<template>
  <div class="console">
    <header class="app-header"><div class="brand-mark" aria-hidden="true">h<span>_</span></div><div class="brand"><strong>AI Agent Harness Platform</strong><span>智能体运行控制台</span></div><div class="header-right"><span class="environment">{{ environment }}</span><span class="connection" :class="{offline: !connected}"><i></i>{{ connected ? '运行环境正常' : loading ? '正在连接运行环境' : '运行环境离线' }}</span></div></header>
    <div class="console-grid">
      <aside class="sidebar" aria-label="任务与运行">
        <div class="sidebar-title"><span class="eyebrow">工作区</span><div class="section-heading"><h2>任务与运行</h2><span class="count">{{ tasks.length }}</span></div></div>
        <button class="new-task" :disabled="!!busy" @click="newTask"><span>＋</span> 新建任务</button>
        <div class="list-label">任务 <button aria-label="刷新任务与运行" :disabled="!!busy || refreshing" @click="refresh">↻</button></div>
        <nav class="task-list" aria-label="任务列表"><button v-for="task in tasks" :key="task.id" class="task-item" :class="{active: task.id === selectedId && !creating}" :disabled="!!busy" @click="creating = false; select(task.id)"><span class="task-number">任务 / {{ String(task.id).padStart(3, '0') }} <time v-if="task.created_at">{{ date(task.created_at) }}</time></span><strong>{{ task.title }}</strong><div class="task-status"><span v-if="runs[task.id]" class="last-run">Run · {{ runs[task.id].status }}</span><span v-else class="last-run">暂无已知运行</span></div></button></nav>
        <p v-if="!tasks.length" class="sidebar-empty">{{ loading ? '正在加载任务…' : connected ? '创建后的任务会显示在这里。' : '连接后端以加载任务。' }}</p>
        <div class="sidebar-bottom"><form class="open-run" @submit.prevent="openRun(openId)"><label for="run-id">打开历史运行</label><div><input id="run-id" v-model="openId" placeholder="输入 Run ID" maxlength="128" :disabled="!!busy"><button aria-label="打开运行" :disabled="!!busy || !openId.trim()">↗</button></div></form><p>Run ID 会保存在当前浏览器中。<br>所有状态均来自真实运行时。</p><div class="local-footer"><span class="dot"></span> 本地工作区</div></div>
      </aside>
      <main class="workspace">
        <div class="workspace-toolbar"><span>工作区 <span class="slash">/</span> <strong>{{ creating ? '新建任务' : '运行工作区' }}</strong></span><button class="text-button" :disabled="!!busy || refreshing" @click="refresh">↻ 刷新</button></div>
        <div v-if="error" role="alert" class="error-banner">{{ error }}</div>
        <div v-if="busy" role="status" class="busy-banner"><span class="dot"></span>{{ busy }}<small v-if="startingTask">运行结束或暂停后，运行时会返回 ID 和执行轨迹。</small></div>
        <section v-if="creating" class="new-task-panel"><span class="eyebrow">新建执行任务</span><h1>新建任务</h1><p>明确任务目标，由运行时协调工作流。</p><form @submit.prevent="submit"><label for="title">标题</label><input id="title" ref="titleInput" v-model="title" placeholder="输入清晰、具体的任务标题" maxlength="255" required :disabled="!!busy"><label for="instruction">任务指令</label><textarea id="instruction" v-model="instruction" rows="7" placeholder="描述任务、执行范围和预期结果。" required :disabled="!!busy"></textarea><div class="form-actions"><button type="button" class="button secondary" :disabled="!!busy" @click="creating = false">取消</button><button class="button primary" :disabled="!!busy || !title.trim() || !instruction.trim()">创建任务</button></div></form></section>
        <template v-else-if="selected">
          <div class="run-heading"><span class="eyebrow">任务 / {{ String(selected.id).padStart(3, '0') }}</span><div class="title-row"><h1>{{ selected.title }}</h1><button class="button primary" :disabled="!!busy || !connected || ['RUNNING', 'INTERRUPTED'].includes(run?.status ?? '')" @click="start"><span aria-hidden="true">▷</span> 启动运行</button></div><p class="instruction">{{ selected.instruction }}</p></div>
          <section class="run-summary" aria-label="运行状态"><div><span class="metric-label">运行状态</span><StatusBadge v-if="run" :status="run.status" /><strong v-else>{{ startingTask ? '等待响应' : '尚未启动' }}</strong></div><div><span class="metric-label">当前阶段</span><strong class="mono">{{ phase }}</strong></div><div><span class="metric-label">模型调用</span><strong class="mono">{{ run?.usage.calls ?? '—' }}<small v-if="run"> 次</small></strong></div></section>
          <div v-if="run" class="run-identifiers"><span>RUN <code :title="run.run_id">{{ short(run.run_id) }}</code></span><span>THREAD <code :title="run.thread_id">{{ short(run.thread_id) }}</code></span></div>
          <WorkflowStepper :run="run" />
          <ApprovalCard v-if="run?.status === 'INTERRUPTED' && approval" :approval="approval" :busy="!!busy" @decide="decide" @resume="resume" />
          <section v-else-if="run?.status === 'INTERRUPTED'" class="panel paused-panel"><h2>Checkpoint 已暂停</h2><p>{{ run.pending_approval_id ? '暂时无法获取审批详情，请刷新后再操作。' : '从已保存的 Checkpoint 继续执行。' }}</p><button v-if="!run.pending_approval_id" class="button primary" :disabled="!!busy" @click="resume">Resume 工作流</button></section>
          <section class="panel result-panel" aria-label="最终结果"><div class="section-heading"><h2>{{ run?.status === 'FAILED' ? '执行已停止' : '最终结果' }}</h2><span class="result-symbol" :class="{success: run?.status === 'COMPLETED'}">{{ run?.status === 'COMPLETED' ? '✓' : '↳' }}</span></div><div v-if="run?.output" class="result-content">{{ run.output.slice(0, 3000) }}</div><div v-else-if="run?.error" class="result-error"><code>{{ run.error.slice(0, 200) }}</code><p>工作流未完成，请查看执行轨迹了解原因。</p></div><p v-else class="result-placeholder">{{ run?.status === 'INTERRUPTED' ? '等待审批决定及 Checkpoint Resume。' : run?.status === 'RUNNING' || startingTask ? '运行时正在执行，结果就绪后会显示在这里。' : '启动运行后，这里将显示最终结果。' }}</p><footer v-if="run && (run.usage.prompt_tokens > 0 || run.usage.completion_tokens > 0)" class="token-footer"><span>用量</span><span><b>{{ run.usage.prompt_tokens.toLocaleString() }}</b> 输入 tokens</span><span><b>{{ run.usage.completion_tokens.toLocaleString() }}</b> 输出 tokens</span></footer></section>
          <div class="capability-strip"><span><i class="tool-label cli">CLI</i> 受控执行</span><span><i class="tool-label mcp">MCP</i> 工具连接</span><span><i class="tool-label write">WRITE</i> 受控变更</span></div>
        </template>
        <section v-else class="welcome"><span class="welcome-icon">⌘</span><span class="eyebrow">AGENT 工作区</span><h1>AGENT RUNTIME<br>READY</h1><p>{{ connected ? '创建任务以启动一个可追踪的 Agent Workflow。' : '请启动后端，再刷新以连接工作区。' }}</p><button class="button primary" :disabled="!connected" @click="newTask">＋ 新建任务</button><div class="welcome-capabilities"><span>01 <strong>LangGraph 工作流</strong></span><span>02 <strong>CLI + MCP</strong></span><span>03 <strong>Checkpoint / Resume</strong></span><span>04 <strong>人工审批</strong></span></div></section>
        <footer class="workspace-footer">AGENT 运行时 <span>结构化行动 · 受控执行</span></footer>
      </main>
      <TracePanel :events="trace" :status="run?.status" />
    </div>
  </div>
</template>
