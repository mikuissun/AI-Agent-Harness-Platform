<script setup lang="ts">
import { computed, ref } from 'vue'
import type { TraceEvent } from '../api/types'
const props = defineProps<{ events: TraceEvent[]; status?: string }>()
const filter = ref('All')
const labels: Record<string, string> = { All: '全部', Agent: 'Agent', Tool: '工具', Governance: '治理', Workflow: '工作流', Retry: '重试' }
function category(e: TraceEvent) {
  if (/APPROVAL|PERMISSION/.test(e.event_type)) return 'Governance'
  if (/RETRY/.test(e.event_type)) return 'Retry'
  if (/^AGENT/.test(e.event_type)) return 'Agent'
  if (/^TOOL/.test(e.event_type)) return 'Tool'
  return 'Workflow'
}
function toolKind(e: TraceEvent) { return e.name.startsWith('mcp.') ? 'MCP' : e.name === 'write_text_file' ? 'WRITE' : 'CLI' }
const visible = computed(() => props.events.filter(e => filter.value === 'All' || category(e) === filter.value).slice(-100))
</script>
<template>
  <aside class="trace-panel" aria-label="执行轨迹">
    <div class="trace-heading"><span class="eyebrow">可观测性</span><div class="section-heading"><h2>执行轨迹</h2><span class="count">{{ events.length }}</span></div><p>每一步都有可追踪记录。</p></div>
    <div class="trace-filters" aria-label="轨迹筛选"><button v-for="name in ['All', 'Agent', 'Tool', 'Governance', 'Workflow', 'Retry']" :key="name" :class="{selected: filter === name}" @click="filter = name">{{ labels[name] }}</button></div>
    <ol v-if="visible.length" class="timeline"><li v-for="event in visible" :key="event.sequence" :class="[category(event).toLowerCase(), category(event) === 'Tool' ? toolKind(event).toLowerCase() : '', { unsuccessful: !event.success, succeeded: event.success && /COMPLETED$/.test(event.event_type) }]">
      <span class="event-icon" aria-hidden="true">{{ category(event) === 'Agent' ? '◎' : category(event) === 'Tool' ? '⌘' : category(event) === 'Governance' ? '◇' : '↳' }}</span>
      <div class="event-body"><div class="event-meta"><span>{{ labels[category(event)] }}</span><code>#{{ String(event.sequence).padStart(2, '0') }} · i{{ event.iteration }}</code></div>
        <h3><span v-if="category(event) === 'Tool' || event.name === 'write_text_file'" class="tool-label" :class="toolKind(event).toLowerCase()">{{ toolKind(event) }}</span>{{ event.name.replace('mcp.', '') }}</h3>
        <span class="event-type">{{ event.event_type }}</span><p>{{ event.summary.slice(0, 200) }}</p>
      </div>
    </li></ol>
    <div v-else class="trace-empty"><div class="trace-placeholder">⌘</div><h3>{{ events.length ? '暂无匹配事件' : '等待执行' }}</h3><p>{{ events.length ? '请选择其他事件类型。' : 'Agent 活动和工具调用会在运行后显示在这里。' }}</p></div>
    <footer class="trace-footer"><span class="dot"></span> 受控轨迹 · 不展示原始 Tool 输出</footer>
  </aside>
</template>
