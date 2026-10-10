<script setup>
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
const props = defineProps({ task: { type: Object, required: true }, busy: Boolean, current: Boolean, agentic: Boolean, resumed: Boolean })
const emit = defineEmits(['followup'])
const status = computed(() => ({ waiting_input: '等待补充', running: '执行中', completed: '已完成', failed: '未完成', cancelled: '已停止', no_evidence: '资料不足' }[props.task.status] || '未完成'))
const isPaper = computed(() => props.task.kind === 'paper_assemble')
const types = { choice: '选择题', fill_blank: '填空题', short_answer: '简答题' }
const difficulty = { easy: '简单', medium: '中等', hard: '困难' }
</script>

<template>
  <section class="agent-task" aria-label="复习任务" aria-live="polite">
    <div class="task-heading"><strong>{{ isPaper ? '组卷任务' : '出题任务' }}</strong><span>{{ resumed && task.status === 'running' && !busy ? '检查点待继续' : status }}</span></div>
    <p>{{ task.goal }}</p>
    <template v-if="isPaper">
      <p class="task-spec">{{ task.spec?.title || '待确认试卷名称' }} · {{ task.spec?.count || '待确认' }} 道 · {{ task.spec?.total_score || 0 }} 分 · {{ task.spec?.save_requested ? '保存试卷' : '仅预览' }}</p>
      <ul class="task-blueprint"><li v-for="(rule, index) in task.spec?.rules || []" :key="index">{{ types[rule.question_type] }} {{ rule.count }} 道 · {{ difficulty[rule.difficulty] }} · 每题 {{ rule.score ?? '待确认' }} 分</li></ul>
      <p class="task-spec">{{ task.spec?.allow_generate ? '优先复用题库，缺题时据源补题' : '只复用题库，缺题时停止' }}<template v-if="task.assembly"> · 复用 {{ task.assembly.reused_count }} 题，补题 {{ task.assembly.generated_count }} 题</template></p>
    </template>
    <p v-else class="task-spec">{{ task.spec?.topic || '待确认章节或知识点' }} · {{ task.spec?.count || '待确认' }} 道 · {{ types[task.spec?.question_type] || '待确认题型' }} · {{ difficulty[task.spec?.difficulty] || '中等' }} · {{ task.spec?.save_requested ? '保存草稿' : '仅预览' }}</p>
    <ol class="task-steps"><li v-for="step in task.steps || []" :key="step.name" :class="{ done: step.done }">{{ step.done ? '✓' : '○' }} {{ step.label }}</li></ol>
    <p v-for="issue in task.issues || []" :key="issue" class="task-issue">{{ issue }}</p>
    <p v-if="task.status === 'failed' && !agentic" class="task-issue">工具决策未完成，未转为普通问答。</p>
    <details v-for="(draft, index) in task.drafts || []" :key="index" class="task-draft">
      <summary>{{ index + 1 }}. {{ draft.stem }}<span v-if="isPaper"> · {{ draft.paper_score }} 分{{ draft.reuse_id ? '（复用）' : '（补题）' }}</span></summary>
      <ol v-if="draft.options?.length"><li v-for="(option, optionIndex) in draft.options" :key="optionIndex">{{ option }}</li></ol>
      <p>答案：{{ draft.answer }}</p><p>解析：{{ draft.analysis }}</p>
      <small>{{ task.status === 'completed' ? '已通过题目与证据校验' : '草稿尚未完成校验' }}</small>
    </details>
    <div v-if="task.artifacts?.length" class="task-links"><RouterLink v-for="artifact in task.artifacts" :key="artifact.id" :to="{ name: 'question-bank', query: artifact.type === 'paper' ? { paper_id: artifact.id } : { question_id: artifact.id } }">{{ artifact.type === 'paper' ? '打开试卷' : '打开题目' }}：{{ artifact.title }}</RouterLink></div>
    <div v-if="current" class="task-actions">
      <button v-if="busy && task.status === 'running'" type="button" @click="emit('followup', '取消任务')">停止任务</button>
      <button v-if="!busy && (['failed', 'cancelled', 'no_evidence'].includes(task.status) || (resumed && task.status === 'running'))" type="button" @click="emit('followup', '重试刚才的任务')">继续执行</button>
      <button v-if="!busy && task.status === 'completed' && !task.spec?.save_requested" type="button" @click="emit('followup', isPaper ? '保存试卷' : '保存这些题目')">{{ isPaper ? '保存试卷草稿' : '保存为题库草稿' }}</button>
    </div>
  </section>
</template>

<style scoped>
.agent-task { margin-top: 14px; padding: 14px; border: 1px solid var(--sz-border); border-radius: 10px; background: var(--sz-bg-surface); }
.task-heading { display: flex; justify-content: space-between; gap: 12px; }
.task-heading span, .task-spec { color: var(--sz-text-muted); font-size: 13px; }
.task-blueprint { padding-left: 20px; font-size: 13px; line-height: 1.9; }
.task-steps { display: flex; gap: 12px; flex-wrap: wrap; list-style: none; padding: 0; font-size: 13px; }
.task-steps .done { color: var(--sz-accent); }
.task-issue { color: var(--sz-warning); }
.task-draft { padding-block: 9px; border-top: 1px solid var(--sz-border); }
.task-draft summary { cursor: pointer; }
.task-links { display: grid; gap: 8px; margin-top: 12px; }
.task-actions { display: flex; gap: 10px; margin-top: 12px; }
.task-actions button { padding: 7px 12px; border: 1px solid var(--sz-border); border-radius: 8px; background: var(--sz-bg-panel); color: var(--sz-text-main); cursor: pointer; }
</style>
