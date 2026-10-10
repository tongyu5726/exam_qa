<script setup>
defineProps({ assessments: { type: Array, default: () => [] }, retests: { type: Number, default: 0 } })
const labels = { continue: '继续现有计划', review: '追加错项补讲', retest: '补讲后换题复核' }
</script>

<template>
  <section v-if="assessments.length" class="study-feedback" aria-label="作答反馈与后续安排">
    <h4>作答反馈与后续安排</h4>
    <ol><li v-for="(item, index) in assessments" :key="item.quiz_id">
      <strong>第 {{ index + 1 }} 轮 · 答对 {{ item.correct_count }}/{{ item.total_count }} 题 · {{ labels[item.action] }}</strong>
      <p>{{ item.focus }}：{{ item.reason }}</p>
    </li></ol>
    <p>已自动追加 {{ retests }}/2 次重测。结果仅描述本轮作答。</p>
  </section>
</template>

<style scoped>
.study-feedback { padding: 10px 12px; margin-block: 12px; background: var(--sz-bg-surface); border-radius: 8px; font-size: 14px; }
h4 { margin-block: 4px 8px; }
ol { padding-left: 22px; }
p { margin-block: 5px; color: var(--sz-text-muted); }
</style>
