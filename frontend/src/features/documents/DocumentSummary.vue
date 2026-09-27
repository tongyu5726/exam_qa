<script setup>
import { NSelect } from 'naive-ui'
defineProps({ summary: { type: Object, default: () => ({}) }, by: { type: String, default: 'type' } })
defineEmits(['update:by'])
const options = [{ label: '按文件类型', value: 'type' }, { label: '按章节', value: 'chapter' }]
</script>

<template>
  <section class="summary-section">
    <div class="section-head"><h2>资料分类</h2><NSelect :value="by" :options="options" class="summary-select" @update:value="$emit('update:by', $event)" /></div>
    <p v-if="!summary.groups?.length" class="empty">暂无资料可分类。</p>
    <template v-else>
      <p class="muted">{{ by === 'chapter' ? `${summary.total_chunks || 0} 块 · ${summary.groups.length} 个章节` : `${summary.groups.length} 类 · ${summary.total || 0} 份资料` }}</p>
      <div class="summary-grid">
        <article v-for="(group, index) in summary.groups" :key="`${by}-${index}`" class="summary-card">
          <strong>{{ by === 'chapter' ? (group.chapter || '未分类') : (group.label || group.type || '其他') }}</strong>
          <span>{{ by === 'chapter' ? `${group.chunk_count || 0} 块` : `${group.documents?.length || 0} 份` }}</span>
          <ul v-if="by === 'chapter'"><li v-for="file in group.source_files || []" :key="file">{{ file }}</li></ul>
          <ul v-else><li v-for="doc in group.documents || []" :key="doc.id">{{ doc.filename || doc.id }} <span>{{ doc.chunk_count || 0 }} 块</span></li></ul>
        </article>
      </div>
    </template>
  </section>
</template>

<style scoped>
.summary-section { padding-top: 15px; border-top: 1px solid var(--sz-border); }
.summary-select { width: 160px; }
.summary-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.summary-card { padding: 14px; border: 1px solid var(--sz-border); border-radius: 10px; }
.summary-card > span { float: right; color: var(--sz-text-muted); }
.summary-card ul { margin: 10px 0 0; padding-left: 18px; color: var(--sz-text-muted); overflow-wrap: anywhere; }
.summary-card li { margin-top: 4px; }
@media(max-width: 700px) { .summary-grid { grid-template-columns: 1fr; } }
</style>
