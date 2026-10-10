<script setup>
import { computed } from 'vue'
import { apiUrl } from '@/api/client.js'
const props = defineProps({ citations: { type: Array, default: () => [] }, courseId: { type: String, default: '' } })
const hasCandidates = computed(() => props.citations.some(c => c.evidence_role !== 'model_selected'))
function sourceUrl(citation, courseId) {
  if (!citation.doc_id) return ''
  const url = apiUrl(`/documents/${encodeURIComponent(citation.doc_id)}/source`, { course_id: courseId })
  if (citation.page != null) url.hash = `page=${encodeURIComponent(citation.page)}`
  return url.toString()
}
function effectivePeriod(citation) {
  const start = citation.effective_from && citation.effective_from !== '0001-01-01' ? citation.effective_from : ''
  const end = citation.effective_to && citation.effective_to !== '9999-12-31' ? citation.effective_to : ''
  return start || end ? `${start || '起始未标注'} 至 ${end || '结束未标注'}` : '生效期未标注'
}
</script>

<template>
  <details v-if="citations.length" class="citations">
    <summary>{{ hasCandidates ? '查看检索候选资料' : '查看模型选用的资料' }} <span>{{ citations.length }}</span></summary>
    <div class="citation-list">
      <p class="muted">{{ hasCandidates ? '模型未标明使用来源，以下检索片段供核对，可能包含无关内容。' : '这些来源由模型标明使用，仍需对照原文核实结论和适用条件。' }}</p>
      <article v-for="(citation, index) in citations" :key="`${citation.doc_id}-${index}`" class="citation">
        <div class="citation-head"><span class="citation-index">{{ citation.evidence_index ?? index + 1 }}</span><strong>{{ citation.source_file || '未知资料' }}</strong><span class="muted">{{ citation.page != null ? `第 ${citation.page} 页` : '页码未标注' }}{{ citation.section_path || citation.chapter ? ` · ${citation.section_path || citation.chapter}` : '' }}</span></div>
        <p>{{ citation.snippet || '暂无摘要' }}</p>
        <p v-if="hasCandidates && citation.evidence_role === 'model_selected'" class="muted">模型标明使用此片段。</p>
        <div class="evidence-meta">
          <span>{{ citation.source_version ? `版本：${citation.source_version}` : '版本未标注' }}</span>
          <span>{{ effectivePeriod(citation) }}</span>
          <span>{{ citation.applicability_scope && citation.applicability_scope !== 'all' ? `适用场景：${citation.applicability_scope}` : '未限定场景' }}</span>
          <span>{{ citation.authority_label ? `权威标签：${citation.authority_label}` : '权威标签未标注' }}</span>
        </div>
        <p v-if="citation.selection_reason" class="muted">选择依据：{{ citation.selection_reason }}</p>
        <p v-if="citation.source_status === 'stale'" class="muted">来源已失效或无法核实；以下片段是历史依据。</p>
        <a v-if="citation.doc_id" :href="sourceUrl(citation, courseId)" target="_blank" rel="noopener noreferrer">打开源文档 ↗</a>
      </article>
    </div>
  </details>
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
.evidence-meta { display: flex; flex-wrap: wrap; gap: 5px 14px; font-size: 12px; color: var(--sz-text-muted); }
</style>
