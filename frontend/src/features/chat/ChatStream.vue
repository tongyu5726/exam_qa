<script setup>
import { computed, ref } from 'vue'
import MathAnswer from './MathAnswer.vue'
import CitationList from './CitationList.vue'
import StudyGoalCard from './StudyGoalCard.vue'
import AgentTaskCard from './AgentTaskCard.vue'
import AgentToolTrace from './AgentToolTrace.vue'

const props = defineProps({
  turns: { type: Array, default: () => [] },
  live: { type: Object, default: null },
  checkpoint: { type: Object, default: null },
  mode: { type: String, default: 'auto' },
  courseId: { type: String, default: '' },
  busy: Boolean,
})

const emit = defineEmits(['followup'])
const currentTaskIndex = computed(() => props.live?.agentTask || props.checkpoint?.task ? -1 : props.turns.findLastIndex((turn) => turn.agentTask))
const streamBox = ref(null)
const modeLabels = { auto: '自动识别', qa: '自由问答', concept: '知识点', chapter: '章节概览' }
const isConversation = (turn) => turn.responseKind === 'conversation' || turn.intent?.task === 'conversation' || turn.task === 'conversation'
const modeLabel = (turn) => isConversation(turn) ? '交流' : modeLabels[turn.mode] || '自由问答'
const emptyText = computed(() => ({
  chapter: '选择课程后输入章节名，按章生成概览',
  concept: '选择课程后输入知识点，查看资料依据',
}[props.mode] || '选择课程后提问；左侧可切换历史对话'))

function cleanAnswer(answer, citations = []) {
  const sources = citations.map((item) => String(item.source_file || '').replace(/\.(pdf|txt|md|docx?|pptx)$/i, '')).filter(Boolean)
  return String(answer || '').replace(/\s*【([^】]{1,500})】/g, (full, label) => (/正文证据|公式证据/.test(label) || sources.some((source) => label.includes(source))) ? '' : full).replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim()
}

function liveAnswer(turn) {
  return cleanAnswer(turn.answer, turn.citations) || ({
    retrieving: '检索中…', reasoning: '模型正在推理…', routing: '正在理解请求…', working: '正在执行…',
  }[turn.phase] || '生成中…')
}

function scrollEnd() {
  if (streamBox.value) streamBox.value.scrollTop = streamBox.value.scrollHeight
}

defineExpose({ scrollEnd })
</script>

<template>
  <div ref="streamBox" class="chat-stream" role="log" aria-label="对话内容">
    <div v-if="!turns.length && !live && !checkpoint" class="chat-empty">
      <p>{{ emptyText }}</p>
      <p>检索已启用向量 + BM25 混合召回</p>
    </div>
    <article v-for="(turn, index) in turns" :key="index" class="chat-turn">
      <div class="question-bubble"><span>{{ modeLabel(turn) }}</span><p>{{ turn.question }}</p><small v-if="turn.scope && !isConversation(turn)">查询范围：{{ turn.intent?.scenario || turn.scope.scenario || 'all' }} · {{ turn.intent?.as_of || turn.scope.as_of || '不限日期' }}</small></div>
      <div class="answer-bubble" :data-grounded="isConversation(turn) ? null : turn.grounded">
        <MathAnswer :text="cleanAnswer(turn.answer, turn.citations)" final />
        <CitationList :citations="turn.citations" :course-id="courseId" />
        <AgentToolTrace :calls="turn.toolCalls || []" :agentic="turn.agentic" :has-task="!!turn.agentTask" />
        <StudyGoalCard v-if="turn.agentTask?.kind === 'study_goal'" :task="turn.agentTask" :busy="busy" :current="index === currentTaskIndex" :course-id="courseId" @followup="emit('followup', $event)" />
        <AgentTaskCard v-else-if="turn.agentTask" :task="turn.agentTask" :busy="busy" :current="index === currentTaskIndex" :agentic="turn.agentic" @followup="emit('followup', $event)" />
      </div>
    </article>
    <article v-if="checkpoint?.task && !live" class="chat-turn" aria-label="已恢复的任务检查点">
      <div class="answer-bubble">
        <p>{{ checkpoint.hint }}</p>
        <StudyGoalCard v-if="checkpoint.task.kind === 'study_goal'" :task="checkpoint.task" :busy="busy" current resumed :course-id="courseId" @followup="emit('followup', $event)" />
        <AgentTaskCard v-else :task="checkpoint.task" :busy="busy" current resumed @followup="emit('followup', $event)" />
      </div>
    </article>
    <article v-if="live" class="chat-turn">
      <div class="question-bubble"><span>{{ modeLabel(live) }}</span><p>{{ live.question }}</p></div>
      <div class="answer-bubble" :data-grounded="isConversation(live) ? null : live.grounded">
        <MathAnswer :text="liveAnswer(live)" :final="live.final" />
        <CitationList :citations="live.citations" :course-id="courseId" />
        <AgentToolTrace :calls="live.toolCalls || []" :agentic="live.agentic" :has-task="!!live.agentTask" />
        <StudyGoalCard v-if="live.agentTask?.kind === 'study_goal'" :task="live.agentTask" :busy="busy" current :course-id="courseId" @followup="emit('followup', $event)" />
        <AgentTaskCard v-else-if="live.agentTask" :task="live.agentTask" :busy="busy" current :agentic="live.agentic" @followup="emit('followup', $event)" />
      </div>
    </article>
  </div>
</template>

<style scoped>
.chat-stream { flex: 1; min-height: 0; overflow-y: auto; padding: 16px clamp(16px, 4vw, 50px); }
.chat-empty { margin: 42px auto; max-width: 540px; text-align: center; color: var(--sz-text-muted); font-size: 16px; letter-spacing: .08em; }
.chat-empty p { margin: 0 0 5px; }
.chat-turn { display: grid; gap: 12px; margin-bottom: 26px; }
.question-bubble { justify-self: end; max-width: min(85%, 620px); background: var(--sz-bg-surface); border-radius: 16px 16px 4px 16px; padding: 12px 16px; }
.question-bubble span { color: var(--sz-accent); font-size: 12px; }
.question-bubble p { margin: 5px 0 0; white-space: pre-wrap; }
.answer-bubble { max-width: 860px; padding: 15px 18px; border-left: 3px solid var(--sz-accent); background: var(--sz-bg-panel); border-radius: 0 10px 10px 0; }
.answer-bubble[data-grounded="false"] { border-color: var(--sz-warning); }
@media(max-width: 720px) { .chat-stream { padding: 16px; } }
</style>
