<script setup lang="ts">
import { computed } from 'vue'
import type { Run } from '../api/types'
const props = defineProps<{ run?: Run }>()
const steps = ['ANALYZE', 'PLAN', 'EXECUTE', 'TEST', 'REVIEW']
const events = computed(() => props.run?.trace ?? [])
const hasFix = computed(() => events.value.some(e => e.name === 'FIX' && e.event_type === 'NODE_STARTED'))
function state(name: string) {
  const related = events.value.filter(e => e.name === name && ['NODE_STARTED', 'NODE_COMPLETED'].includes(e.event_type))
  const last = related.at(-1)
  if (!last) return 'pending'
  if (last.event_type === 'NODE_COMPLETED') return last.success ? 'done' : 'failed'
  return props.run?.status === 'FAILED' ? 'failed' : 'active'
}
</script>
<template>
  <section class="panel workflow" aria-label="工作流" :class="{ interrupted: run?.status === 'INTERRUPTED' }">
    <div class="section-heading"><h2>工作流</h2><span class="micro">LANGGRAPH</span></div>
    <ol class="steps"><li v-for="(name, index) in steps" :key="name" :class="state(name)"><span class="step-node">{{ state(name) === 'done' ? '✓' : String(index + 1).padStart(2, '0') }}</span><strong>{{ name }}</strong><small>{{ { done: '已完成', failed: '失败', active: run?.status === 'INTERRUPTED' ? '等待审批' : '执行中', pending: '未开始' }[state(name)] }}</small></li></ol>
    <div v-if="hasFix" class="branch">↳ FIX → TEST → REVIEW <span class="micro">修复分支</span></div>
    <div v-if="run?.status !== 'COMPLETED' && events.some(e => e.event_type === 'APPROVAL_REQUIRED')" class="checkpoint-path">
      <span>◇ 等待人工审批</span><span>→</span><span :class="{ muted: !events.some(e => e.event_type === 'WORKFLOW_RESUMED') }">↻ Checkpoint / Resume</span>
      <span v-if="events.some(e => e.event_type === 'APPROVAL_REJECTED')" class="denied-label">已拒绝</span>
    </div>
  </section>
</template>
