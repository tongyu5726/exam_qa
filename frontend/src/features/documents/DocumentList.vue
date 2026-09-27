<script setup>
import { NButton, NTag } from 'naive-ui'
import { apiUrl } from '@/api/client.js'

defineProps({ documents: { type: Array, default: () => [] }, courseId: { type: String, default: '' }, busyId: { type: String, default: '' } })
defineEmits(['remove'])
const statusText = { done: '已入库', failed: '失败', pending: '待处理', processing: '处理中' }
const statusType = { done: 'success', failed: 'error', pending: 'warning', processing: 'info' }
function sourceUrl(id, courseId) { return apiUrl(`/documents/${encodeURIComponent(id)}/source`, { course_id: courseId }).toString() }
</script>

<template>
  <section class="document-section">
    <div class="section-head"><h2>已入库</h2><span class="muted">{{ documents.length }} 份</span></div>
    <p v-if="!documents.length" class="empty">暂无资料，上传或扫描后显示在此。</p>
    <div v-else class="document-list">
      <article v-for="doc in documents" :key="doc.id" class="document-row">
        <div class="document-details"><strong>{{ doc.filename || doc.id }}</strong><div class="document-tags"><NTag size="small" :type="statusType[doc.status] || 'default'">{{ statusText[doc.status] || doc.status || '未知' }}</NTag><NTag v-if="doc.chunk_count != null" size="small">{{ doc.chunk_count }} 块</NTag></div></div>
        <div class="document-actions"><a :href="sourceUrl(doc.id, courseId)" target="_blank" rel="noopener noreferrer">查看源文档</a><NButton size="small" text type="error" :loading="busyId === doc.id" @click="$emit('remove', doc)">删除</NButton></div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.document-list { display: grid; gap: 8px; }
.document-row { display: flex; align-items: center; justify-content: space-between; gap: 14px; padding: 11px 14px; border: 1px solid var(--sz-border); border-radius: 10px; background: var(--sz-bg-panel); }
.document-details { min-width: 0; overflow-wrap: anywhere; }
.document-tags, .document-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.document-tags { margin-top: 7px; }
.document-actions { flex: none; }
.document-actions a { color: var(--sz-accent); }
@media(max-width: 620px) { .document-row { display: block; } .document-actions { margin-top: 12px; } }
</style>
