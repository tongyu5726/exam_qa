<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useDialog, useMessage } from 'naive-ui'
import { askStream } from '@/api/askStream.js'
import { apiGet, apiPost, apiDelete } from '@/api/client.js'
import { useCourseStore } from '@/stores/course.js'
import { useConversationStore } from '@/stores/conversation.js'
import ConversationSidebar from './ConversationSidebar.vue'
import ChatStream from './ChatStream.vue'
import ChatComposer from './ChatComposer.vue'
import EvidenceScope from './EvidenceScope.vue'
import { useConversationRestore } from './useConversationRestore.js'
import AgentExecution from './AgentExecution.vue'
import { useAgentExecution } from './useAgentExecution.js'
import { historyTurns } from './conversationHistory.js'

const course = useCourseStore()
const conversations = useConversationStore()
const message = useMessage()
const dialog = useDialog()
const live = ref(null)
const asking = ref(false)
const recovery = useConversationRestore(course, conversations, asking)
const execution = useAgentExecution(course, conversations, recovery, finishResumed, (cause) => message.error(cause.message))
const stream = ref(null)
const scenario = ref('all')
const asOf = ref('')
const fixedScope = ref(false)
const active = computed(() => conversations.list.find((item) => item.id === conversations.activeId))
let controller

function finishResumed(data) {
  if (data.execution?.status !== 'completed') {
    if (live.value) { live.value.answer = data.answer; live.value.agentTask = data.task; live.value.execution = data.execution }
    return
  }
  if (live.value) {
    conversations.append({ ...live.value, answer: data.answer, citations: data.citations, grounded: data.grounded,
      intent: data.intent, agentTask: data.task, responseKind: data.response_kind, agentic: data.agentic, toolCalls: data.tool_calls, execution: data.execution })
    live.value = null
    recovery.clear()
  } else {
    // Reload the published history after a checkpoint was restored without a local live turn.
    const conversationId = conversations.activeId
    const courseId = course.currentId
    apiGet(`/conversations/${encodeURIComponent(conversationId)}`, { course_id: courseId }).then((detail) => {
      if (course.currentId !== courseId || conversations.activeId !== conversationId) return
      const turns = historyTurns(detail.messages)
      const published = turns.find((turn) => turn.executionId === data.execution.id)
      if (published) Object.assign(published, { agentTask: data.task, toolCalls: data.tool_calls, agentic: data.agentic,
        responseKind: data.response_kind, intent: data.intent })
      conversations.restoreTurns(conversationId, turns, courseId)
      recovery.clear()
    }).catch((cause) => message.error(cause.message))
  }
}

async function scrollEnd() {
  await nextTick()
  stream.value?.scrollEnd()
}

function newConversation() {
  if (!course.currentId) return
  controller?.abort()
  conversations.create()
  live.value = null
}

function select(id) {
  controller?.abort()
  conversations.select(id)
  live.value = null
}

function remove(item) {
  const courseId = course.currentId
  dialog.warning({
    title: '删除对话', content: `删除「${item.title || '新对话'}」？`,
    positiveText: '删除', negativeText: '取消',
    onPositiveClick: async () => {
      try {
        if (item.serverBacked || item.turns.length) await apiDelete(`/conversations/${encodeURIComponent(item.id)}`, { course_id: courseId })
        conversations.remove(item.id, courseId)
      } catch (cause) { if (cause.status === 404) conversations.remove(item.id, courseId); else message.error(cause.message) }
    },
  })
}

async function submit(text) {
  const courseId = course.currentId
  if (!text || !courseId || asking.value || recovery.loading.value || execution.blocked.value) return
  if (!active.value) conversations.create(text)
  const conversationId = conversations.activeId
  const mode = conversations.askMode
  asking.value = true
  recovery.clear()
  const scope = { scenario: fixedScope.value ? scenario.value : null, as_of: fixedScope.value ? asOf.value || null : null, scope_override: fixedScope.value }
  live.value = { question: text, answer: '', citations: [], grounded: null, mode, scope, phase: 'routing', final: false, task: '' }
  controller = new AbortController()
  const requestController = controller
  scrollEnd()
  try {
    await askStream({ question: text, course_id: courseId, mode, conversation_id: conversationId, breakpoints: execution.breakpoints.value, ...scope }, (event) => {
      if (course.currentId !== courseId || conversations.activeId !== conversationId) return
      execution.accept(event)
      if (event.type === 'phase') {
        live.value.phase = event.phase
        live.value.mode = event.intent?.mode || live.value.mode
        live.value.task = event.intent?.task || live.value.task
      } else if (event.type === 'task') live.value.agentTask = event.data
      else if (event.type === 'tool') {
        live.value.toolCalls = [...(live.value.toolCalls || []), event.data]
        live.value.agentic = !!event.agentic
      }
      else if (event.type === 'delta') live.value.answer += event.text || ''
      else if (event.type === 'done') {
        const data = event.data || {}
        live.value.answer = data.answer || live.value.answer
        live.value.citations = data.citations || []
        live.value.grounded = !!data.grounded
        live.value.task = data.intent?.task || live.value.task
        live.value.intent = data.intent || null
        live.value.agentTask = data.task || live.value.agentTask
        live.value.responseKind = data.response_kind || 'answer'
        live.value.agentic = !!data.agentic
        live.value.toolCalls = data.tool_calls || []
        live.value.final = true
        live.value.execution = data.execution || null
      } else if (event.type === 'error') throw new Error(event.message || '问答失败')
      scrollEnd()
    }, requestController.signal)
    if (live.value?.final && course.currentId === courseId && conversations.activeId === conversationId) {
      if (!live.value.execution || live.value.execution.status === 'completed') {
        conversations.append({ ...live.value })
        live.value = null
      }
    } else if (course.currentId === courseId && conversations.activeId === conversationId) {
      throw new Error('回答意外中断，请重试')
    }
  } catch (cause) {
    if (cause.name !== 'AbortError') {
      message.error(cause.message)
      if (live.value) {
        live.value.answer = cause.message
        live.value.grounded = false
        live.value.final = true
      }
    }
  } finally {
    if (controller === requestController) { asking.value = false; controller = undefined }
  }
}

async function followup(text) {
  if (text === '取消任务' && execution.blocked.value) return execution.control('cancel')
  if (text !== '取消任务' || !asking.value) return submit(text)
  const courseId = course.currentId
  const conversationId = conversations.activeId
  controller?.abort()
  const cancelController = new AbortController()
  controller = cancelController
  try {
    const data = await apiPost('/ask', { question: text, course_id: courseId, conversation_id: conversationId })
    if (course.currentId !== courseId || conversations.activeId !== conversationId) return
    conversations.append({ question: text, answer: data.answer, grounded: data.grounded,
      citations: data.citations, agentTask: data.task, responseKind: data.response_kind, agentic: data.agentic })
    live.value = null
  } catch (cause) { message.error(cause.message) }
  finally { if (controller === cancelController) { controller = undefined; asking.value = false } }
}

watch(() => course.currentId, () => {
  controller?.abort()
  live.value = null
  asking.value = false
})
onUnmounted(() => controller?.abort())
</script>

<template>
  <div class="chat-layout" :style="{ '--history-width': `${conversations.historyWidth}px` }">
    <ConversationSidebar
      :items="conversations.list" :active-id="conversations.activeId" :course-id="course.currentId"
      :collapsed="conversations.historyCollapsed" @new="newConversation" @select="select" @remove="remove"
      @toggle="conversations.historyCollapsed = !conversations.historyCollapsed"
      @resize="conversations.historyWidth = $event"
    />
    <section class="chat-main">
      <AgentExecution v-model:breakpoints="execution.breakpoints.value" :execution="execution.run.value" :nodes="execution.nodes.value" :checkpoints="execution.checkpoints.value" :busy="execution.controlling.value" @control="execution.control" @inspect="execution.inspect" />
      <p v-if="recovery.notice.value" class="recovery-status" role="status">{{ recovery.notice.value }}</p>
      <p v-if="recovery.loading.value" class="recovery-status" role="status">正在恢复对话和任务检查点…</p>
      <p v-if="recovery.error.value" class="recovery-status" role="status">{{ recovery.error.value }}</p>
      <ChatStream ref="stream" :turns="active?.turns || []" :live="live" :checkpoint="recovery.checkpoint.value" :mode="conversations.askMode" :course-id="course.currentId" :busy="asking || recovery.loading.value || !!execution.blocked.value" @followup="followup" />
      <ChatComposer v-model:mode="conversations.askMode" :busy="asking || execution.controlling.value" :disabled="!course.currentId || recovery.loading.value || !!execution.blocked.value" @submit="submit" />
      <EvidenceScope v-model:scenario="scenario" v-model:as-of="asOf" v-model:fixed="fixedScope" :course-id="course.currentId" :disabled="asking" />
    </section>
  </div>
</template>

<style scoped>
.chat-layout { display: flex; height: 100%; min-height: 0; overflow: hidden; }
.chat-main { flex: 1; min-width: 0; display: flex; flex-direction: column; background: var(--sz-bg-panel); }
.recovery-status { margin: 8px 16px; font-size: 13px; color: var(--sz-text-muted); }
</style>
