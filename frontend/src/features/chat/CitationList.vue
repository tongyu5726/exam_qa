<script setup>
import { apiUrl } from '@/api/client.js'
defineProps({ citations: { type: Array, default: () => [] }, courseId: { type: String, default: '' } })
function sourceUrl(citation, courseId) {
  if (!citation.doc_id) return ''
  const url = apiUrl(`/documents/${encodeURIComponent(citation.doc_id)}/source`, { course_id: courseId })
  if (citation.page != null) url.hash = `page=${encodeURIComponent(citation.page)}`
  return url.toString()
}
</script>

<template>
  <details v-if="citations.length" class="citations"><summary>查看回答依据 <span>{{ citations.length }}</span></summary><div class="citation-list"><article v-for="(citation, index) in citations" :key="`${citation.doc_id}-${index}`" class="citation"><div class="citation-head"><span class="citation-index">{{ index + 1 }}</span><strong>{{ citation.source_file || '未知资料' }}</strong><span class="muted">{{ citation.page != null ? `第 ${citation.page} 页` : '页码未标注' }}{{ citation.section_path || citation.chapter ? ` · ${citation.section_path || citation.chapter}` : '' }}</span></div><p>{{ citation.snippet || '暂无摘要' }}</p><a v-if="citation.doc_id" :href="sourceUrl(citation, courseId)" target="_blank" rel="noopener noreferrer">打开源文档 ↗</a></article></div></details>
</template>

<style scoped>
.citations { margin-top: 14px; border: 1px solid var(--sz-border); border-radius: 10px; overflow: hidden; }
.citations summary { cursor: pointer; padding: 11px 14px; color: var(--sz-accent); font-weight: 600; }
.citations summary span { margin-left: 5px; color: var(--sz-text-muted); }
.citation-list { display: grid; gap: 8px; padding: 0 12px 12px; }
.citation { background: var(--sz-bg-surface); padding: 12px; border-radius: 8px; }
.citation-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 7px; }
.citation-index { color: var(--sz-accent); }
.citation p { margin: 9px 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.citation a { color: var(--sz-accent); }
</style>
