<script setup>
defineProps({ review: { type: Object, required: true }, busy: Boolean })
const emit = defineEmits(['open', 'recommend'])
</script>

<template>
  <section class="panel">
    <h2>今日复习</h2>
    <p class="muted">最近 {{ review.stats.period_days }} 天：提交 {{ review.stats.submitted }} 次 · 错误 {{ review.stats.wrong }} 次 · 重练正确 {{ review.stats.retrained_correct }} 次 · 待自评 {{ review.stats.awaiting_assessment }} 次</p>
    <p class="muted">主观题结果来自你的自评；这些记录不代表课程掌握度。</p>
    <p v-if="!review.recommendations.length" class="empty">暂时没有待练习题目。可到题库按资料出题，或回看作答历史。</p>
    <div v-for="item in review.recommendations" :key="`${item.question_id}-${item.action}`" class="review-row"><div><strong>{{ item.stem }}</strong><p class="muted">{{ item.reason }}</p></div><button :disabled="busy" @click="emit('recommend', item)">{{ item.action === 'resume' ? '继续' : item.action === 'retry' ? '重练' : '开始练习' }}</button></div>
  </section>
  <section class="panel">
    <h2>待复习错题 · {{ review.wrong_questions.length }}</h2>
    <p v-if="!review.wrong_questions.length" class="muted">当前没有待复习错题。</p>
    <div v-for="item in review.wrong_questions" :key="item.id" class="review-row"><div><strong>{{ item.question.stem }}</strong><p v-if="item.evidence_status === 'stale'" class="error-text">来源已失效或无法核实，请先核对当前资料。</p></div><button :disabled="busy" @click="emit('open', item.id)">回看依据与重练</button></div>
  </section>
  <section class="panel">
    <h2>作答历史</h2>
    <p class="muted">展示最近 {{ review.history_limit ?? 50 }} 次提交；重新练习会新增记录。</p>
    <p v-if="!review.history.length" class="muted">尚无作答记录，先从今日复习开始。</p>
    <div v-for="item in review.history" :key="item.id" class="review-row"><div><strong>{{ item.question.stem }}</strong><p class="muted">{{ new Date(item.submitted_at).toLocaleString() }} · {{ item.grading_method === 'self' ? '自评' : '自动判分' }} · {{ item.correct === null ? '待自评' : item.correct ? '正确' : '错误' }}</p></div><button :disabled="busy" @click="emit('open', item.id)">查看</button></div>
  </section>
</template>

<style scoped>
.review-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 12px 0; border-top: 1px solid var(--sz-border); }
.review-row p { margin: 4px 0 0; font-size: 13px; }
.review-row button { flex-shrink: 0; padding: 6px 10px; color: var(--sz-accent); background: var(--sz-bg-surface); border: 1px solid var(--sz-border); border-radius: 6px; cursor: pointer; }
.review-row button:disabled { opacity: .5; }
@media(max-width: 600px) { .review-row { align-items: flex-start; flex-direction: column; } }
</style>
