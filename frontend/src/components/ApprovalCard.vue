<script setup lang="ts">
import type { Approval } from '../api/types'
import StatusBadge from './StatusBadge.vue'
defineProps<{ approval: Approval; busy: boolean }>()
defineEmits<{ decide: [decision: 'approve' | 'reject']; resume: [] }>()
</script>
<template>
  <section class="approval-card" aria-label="人工审批">
    <div class="approval-top"><span class="approval-icon">◇</span><div><span class="eyebrow">HUMAN APPROVAL</span><h2>人工审批</h2></div><StatusBadge :status="approval.status" /></div>
    <p>{{ approval.status === 'PENDING' ? '工作流已暂停。请审批此写入请求，批准后才会执行。' : '审批决定已记录，请通过 Checkpoint Resume 继续工作流。' }}</p>
    <dl class="approval-details"><div><dt>工具名称</dt><dd><span class="tool-label write">WRITE</span>{{ approval.tool_name }}</dd></div><div><dt>风险等级</dt><dd>{{ approval.risk_level }}</dd></div><div><dt>目标文件</dt><dd>{{ approval.arguments_summary.path ?? '未提供' }}</dd></div><div><dt>载荷大小</dt><dd>{{ approval.arguments_summary.characters ?? '—' }} 个字符</dd></div><div class="full"><dt>内容 SHA-256</dt><dd class="mono">{{ approval.arguments_summary.sha256 ?? '未提供' }}</dd></div></dl>
    <div class="approval-actions" v-if="approval.status === 'PENDING'"><span>载荷已保存 · 批准前不执行写入</span><button :disabled="busy" class="button reject" @click="$emit('decide', 'reject')">拒绝</button><button :disabled="busy" class="button approve" @click="$emit('decide', 'approve')">批准并 Resume</button></div>
    <div v-else class="approval-actions"><span>{{ approval.status === 'APPROVED' ? '写入结果尚未确定，请刷新状态。' : '使用原 Run 和 Checkpoint。' }}</span><button class="button primary" :disabled="busy || approval.status === 'APPROVED'" @click="$emit('resume')">Resume 工作流</button></div>
  </section>
</template>
