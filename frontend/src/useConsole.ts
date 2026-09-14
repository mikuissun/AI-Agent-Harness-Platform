import { computed, onMounted, onUnmounted, ref } from 'vue'
import { createTask, listTasks } from './api/tasks'
import { getRun, resumeRun, startRun } from './api/runs'
import { decideApproval, getApproval } from './api/approvals'
import { errorMessage, runtimeInfo } from './api/client'
import type { Approval, Run, Task } from './api/types'

const storageKey = 'harness.console.run-ids.v1'
export function useConsole() {
  const tasks = ref<Task[]>([]), selectedId = ref<number>(), runs = ref<Record<number, Run>>({})
  const approval = ref<Approval>(), busy = ref(''), refreshing = ref(false), error = ref('')
  const connected = ref(false), environment = ref('local'), loading = ref(true), startingTask = ref<number>()
  const selected = computed(() => tasks.value.find(t => t.id === selectedId.value))
  const run = computed(() => startingTask.value === selectedId.value ? undefined : runs.value[selectedId.value ?? -1])
  let timer: ReturnType<typeof setTimeout> | undefined, disposed = false
  function remember(value: Run) {
    runs.value[value.task_id] = value
    try { localStorage.setItem(storageKey, JSON.stringify(Object.fromEntries(Object.values(runs.value).map(r => [r.task_id, r.run_id])))) } catch { /* API remains usable without browser storage. */ }
  }
  async function loadApproval(value?: Run) {
    approval.value = undefined
    if (value?.status !== 'INTERRUPTED' || !value.pending_approval_id) return
    const result = await getApproval(value.pending_approval_id)
    if (run.value?.run_id === value.run_id) approval.value = result
  }
  function schedule() {
    clearTimeout(timer)
    if (!disposed && Object.values(runs.value).some(r => r.status === 'RUNNING')) {
      timer = setTimeout(async () => {
        if (!busy.value && !refreshing.value) await refreshRuns(true)
        schedule()
      }, 2000)
    }
  }
  async function refreshRuns(runningOnly = false) {
    const known = Object.values(runs.value).filter(r => !runningOnly || r.status === 'RUNNING')
    const results = await Promise.allSettled(known.map(r => getRun(r.run_id)))
    results.forEach((result, i) => {
      if (result.status === 'fulfilled') remember(result.value)
      else { error.value = errorMessage(result.reason); delete runs.value[known[i].task_id] }
    })
    try { await loadApproval(run.value) } catch (e) { error.value = errorMessage(e) }
  }
  async function refresh() {
    if (busy.value || refreshing.value) return
    refreshing.value = true; error.value = ''
    try {
      const [items, info] = await Promise.all([listTasks(), runtimeInfo()])
      tasks.value = items.slice().sort((a,b) => b.id - a.id)
      environment.value = info.environment; connected.value = true
      if (!selected.value) selectedId.value = tasks.value[0]?.id
      await refreshRuns()
    } catch (e) { error.value = errorMessage(e); connected.value = false }
    finally { refreshing.value = false; loading.value = false; schedule() }
  }
  async function select(id: number) {
    if (busy.value) return
    selectedId.value = id; error.value = ''
    try { await loadApproval(run.value) } catch (e) { error.value = errorMessage(e) }
  }
  async function create(title: string, instruction: string) {
    if (busy.value || !title.trim() || !instruction.trim()) return false
    busy.value = '正在创建任务'; error.value = ''
    try {
      const task = await createTask({ title: title.trim(), instruction: instruction.trim() })
      tasks.value.unshift(task); selectedId.value = task.id; approval.value = undefined
      return true
    } catch (e) { error.value = errorMessage(e); return false }
    finally { busy.value = '' }
  }
  async function start() {
    const task = selected.value
    if (!task || busy.value || ['RUNNING', 'INTERRUPTED'].includes(run.value?.status ?? '')) return
    busy.value = '正在等待运行响应'; startingTask.value = task.id; approval.value = undefined; error.value = ''
    try {
      const result = await startRun(task.id)
      remember(result); startingTask.value = undefined
      await loadApproval(result)
    } catch (e) { error.value = errorMessage(e) }
    finally { busy.value = ''; startingTask.value = undefined; schedule() }
  }
  async function resumeCurrent(value: Run) {
    const result = await resumeRun(value.run_id)
    remember(result); await loadApproval(result)
  }
  async function decide(decision: 'approve' | 'reject') {
    const value = run.value, item = approval.value
    if (busy.value || !value || !item || item.status !== 'PENDING') return
    busy.value = decision === 'approve' ? '正在批准并 Resume' : '正在拒绝并 Resume'; error.value = ''
    try {
      await decideApproval(item.approval_id, decision)
      await loadApproval(value)
      await resumeCurrent(value)
    } catch (e) {
      error.value = errorMessage(e)
      // A failed response may still have recorded the decision. Never repeat it automatically.
      try { await loadApproval(value) } catch { approval.value = undefined }
    } finally { busy.value = ''; schedule() }
  }
  async function resume() {
    const value = run.value
    if (busy.value || !value || value.status !== 'INTERRUPTED') return
    busy.value = '正在执行 Checkpoint Resume'; error.value = ''
    try { await resumeCurrent(value) } catch (e) { error.value = errorMessage(e) }
    finally { busy.value = ''; schedule() }
  }
  async function openRun(id: string) {
    if (busy.value || !id.trim()) return
    busy.value = '正在打开运行'; error.value = ''
    try {
      const value = await getRun(id.trim())
      if (!tasks.value.some(t => t.id === value.task_id)) tasks.value = await listTasks()
      selectedId.value = value.task_id; remember(value); await loadApproval(value)
    } catch (e) { error.value = errorMessage(e) }
    finally { busy.value = ''; schedule() }
  }
  onMounted(async () => {
    await refresh()
    try {
      const stored: unknown = JSON.parse(localStorage.getItem(storageKey) ?? '{}')
      if (stored && typeof stored === 'object' && !Array.isArray(stored)) {
        const ids = Object.values(stored).filter((id): id is string => typeof id === 'string' && id.length <= 128).slice(-50)
        const results = await Promise.allSettled(ids.map(getRun))
        for (const result of results) if (result.status === 'fulfilled' && tasks.value.some(t => t.id === result.value.task_id)) remember(result.value)
        await loadApproval(run.value)
      }
    } catch { /* Stale local IDs do not prevent opening tasks. */ }
    schedule()
  })
  onUnmounted(() => { disposed = true; clearTimeout(timer) })
  return { tasks, selectedId, selected, runs, run, approval, busy, error, connected, environment, loading, refreshing,
    startingTask, refresh, select, create, start, decide, resume, openRun }
}
