<script setup>
import { computed } from 'vue'
import MathAnswer from './MathAnswer.vue'
import CitationList from './CitationList.vue'
import StudyQuiz from './StudyQuiz.vue'
import StudyFeedback from './StudyFeedback.vue'

const props = defineProps({ task: { type: Object, required: true }, busy: Boolean, current: Boolean, resumed: Boolean, courseId: { type: String, default: '' } })
const emit = defineEmits(['followup'])
const disabled = computed(() => props.busy || !props.current)
const labels = { running: '执行中', waiting_input: '等待补充目标', waiting_answer: '等待作答', completed: '本轮计划已完成', failed: '尚未完成', cancelled: '已停止', no_evidence: '缺少有效资料' }
const actions = { explain: '据源讲解', self_test: '自测与反馈', review: '针对错项复习' }
</script>

<template>
  <section class="study-goal" aria-label="复习目标">
    <h3>复习目标 · {{ resumed && task.status === 'running' && !busy ? '检查点待继续' : labels[task.status] || '尚未完成' }}</h3>
    <p>{{ task.goal }}</p>
    <p class="study-meta">范围：{{ task.spec.topic || '待确认' }} · 每轮 {{ task.spec.count }} 道选择题 · {{ task.spec.save_requested ? '已授权保存' : '未授权保存' }}</p>
    <ol class="study-plan"><li v-for="step in task.plan" :key="step.id" :data-status="step.status"><strong>{{ step.status === 'done' ? '✓' : step.status === 'waiting_answer' ? '等待作答' : '待执行' }} · {{ actions[step.action] }}：{{ step.focus }}</strong><p v-if="step.reason">{{ step.reason }}</p></li></ol>
    <details><summary>本轮完成条件</summary><ul><li v-for="criterion in task.criteria" :key="criterion">{{ criterion }}</li></ul><p>完成状态只表示本轮计划已执行，不代表知识掌握程度。</p></details>
    <StudyFeedback :assessments="task.assessments || []" :retests="task.retests || 0" />
    <article v-for="note in task.notes" :key="note.step_id"><h4>{{ note.title }}</h4><MathAnswer :text="note.answer" final /><CitationList :citations="note.citations" :course-id="courseId" /></article>
    <StudyQuiz v-if="task.self_test" :quiz="task.self_test" :disabled="disabled" :course-id="courseId" @submit="emit('followup', $event)" />
    <p v-for="issue in task.issues" :key="issue" role="status">{{ issue }}</p>
    <div v-if="current" class="goal-actions">
      <button :disabled="disabled" @click="emit('followup', '先讲解，再自测，重新规划')">先讲解再自测</button>
      <button :disabled="disabled" @click="emit('followup', '降低难度，重新规划')">降低难度并调整</button>
      <button v-if="['failed', 'cancelled', 'no_evidence', 'running'].includes(task.status)" :disabled="disabled" @click="emit('followup', '重试复习任务')">重试</button>
      <button v-if="task.self_test?.submitted && !task.self_test.saved" :disabled="disabled" @click="emit('followup', '保存本轮自测题')">保存本轮自测题</button>
      <button v-if="!['completed', 'cancelled'].includes(task.status)" :disabled="!current" @click="emit('followup', '取消任务')">停止任务</button>
    </div>
    <p v-if="task.self_test?.saved">本轮自测题已保存至当前课程的题库。</p>
    <p v-else-if="task.artifacts?.length">此前已按授权保存 {{ task.artifacts.length }} 道题；当前轮次尚未保存。</p>
  </section>
</template>

<style scoped>
.study-goal { margin-top: 18px; padding-top: 12px; border-top: 1px solid var(--sz-border); overflow-wrap: anywhere; }
h3 { font-size: 16px; }
.study-meta, .study-plan p, details { color: var(--sz-text-muted); font-size: 13px; }
.study-plan { padding-left: 24px; }
.study-plan li { padding-block: 6px; }
.study-plan p { margin-block: 4px; }
.goal-actions { display: flex; flex-wrap: wrap; gap: 8px; }
button { padding: 8px 10px; cursor: pointer; }
</style>
