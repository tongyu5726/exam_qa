<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useDialog, useMessage } from 'naive-ui'
import { askStream } from '@/api/askStream.js'
import { useCourseStore } from '@/stores/course.js'
import { useConversationStore } from '@/stores/conversation.js'
import ConversationSidebar from './ConversationSidebar.vue'
import ChatStream from './ChatStream.vue'
import ChatComposer from './ChatComposer.vue'

const course = useCourseStore()
const conversations = useConversationStore()
const router = useRouter()
const message = useMessage()
const dialog = useDialog()
const live = ref(null)
const asking = ref(false)
const stream = ref(null)
const active = computed(() => conversations.list.find((item) => item.id === conversations.activeId))
let controller

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
  dialog.warning({
    title: '删除对话', content: `删除「${item.title || '新对话'}」？`,
    positiveText: '删除', negativeText: '取消',
    onPositiveClick: () => conversations.remove(item.id),
  })
}

async function submit(text) {
  const courseId = course.currentId
  if (!text || !courseId || asking.value) return
  if (!active.value) conversations.create(text)
  const conversationId = conversations.activeId
  const mode = conversations.askMode
  asking.value = true
  live.value = { question: text, answer: '', citations: [], grounded: null, mode, phase: 'retrieving', final: false, task: '' }
  controller = new AbortController()
  scrollEnd()
  try {
    await askStream({ question: text, course_id: courseId, mode, conversation_id: conversationId }, (event) => {
      if (course.currentId !== courseId || conversations.activeId !== conversationId) return
      if (event.type === 'phase') {
        live.value.phase = event.phase
        live.value.mode = event.intent?.mode || live.value.mode
        live.value.task = event.intent?.task || live.value.task
      } else if (event.type === 'delta') live.value.answer += event.text || ''
      else if (event.type === 'done') {
        const data = event.data || {}
        live.value.answer = data.answer || live.value.answer
        live.value.citations = data.citations || []
        live.value.grounded = !!data.grounded
        live.value.task = data.intent?.task || live.value.task
        live.value.final = true
      } else if (event.type === 'error') throw new Error(event.message || '问答失败')
      scrollEnd()
    }, controller.signal)
    if (live.value?.final && course.currentId === courseId && conversations.activeId === conversationId) {
      conversations.append({ ...live.value })
      const task = live.value.task
      live.value = null
      if (task === 'question_generate') router.push({ name: 'question-bank', query: { topic: text } })
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
    asking.value = false
    controller = undefined
  }
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
      <ChatStream ref="stream" :turns="active?.turns || []" :live="live" :mode="conversations.askMode" :course-id="course.currentId" />
      <ChatComposer v-model:mode="conversations.askMode" :busy="asking" :disabled="!course.currentId" @submit="submit" />
    </section>
  </div>
</template>

<style scoped>
.chat-layout { display: flex; height: 100%; min-height: 0; overflow: hidden; }
.chat-main { flex: 1; min-width: 0; display: flex; flex-direction: column; background: var(--sz-bg-panel); }
</style>
