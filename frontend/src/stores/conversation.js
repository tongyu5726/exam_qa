import { ref, watch } from 'vue'
import { defineStore } from 'pinia'
import { useCourseStore } from './course.js'

const activeKey = 'sz.active_conversation_id'
const storageKey = (courseId) => `sz.conversations.${courseId || '_none'}`
function readMap() {
  try { const value = JSON.parse(localStorage.getItem(activeKey) || '{}'); return value && typeof value === 'object' && !Array.isArray(value) ? value : {} }
  catch { return {} }
}
function readList(courseId) {
  try { const value = JSON.parse(localStorage.getItem(storageKey(courseId)) || '[]'); return Array.isArray(value) ? value : [] }
  catch { return [] }
}
function makeTitle(question) {
  const first = String(question || '').trim().split(/[。？！?!\n]/)[0].trim()
  return first.length > 36 ? `${first.slice(0, 36)}…` : first || '新对话'
}

export const useConversationStore = defineStore('conversation', () => {
  const course = useCourseStore()
  const list = ref([])
  const activeMap = ref(readMap())
  const askMode = ref(localStorage.getItem('sz.ask_mode') || 'auto')
  const historyCollapsed = ref(localStorage.getItem('sz.history_collapsed') === '1')
  const historyWidth = ref(Number(localStorage.getItem('sz.history_width')) || 260)
  const activeId = ref('')

  function load() {
    const id = course.currentId || '_none'
    list.value = readList(id).sort((a, b) => String(b.updatedAt || '').localeCompare(String(a.updatedAt || '')))
    activeId.value = activeMap.value[id] || ''
  }
  function persist() {
    const id = course.currentId || '_none'
    localStorage.setItem(storageKey(id), JSON.stringify(list.value))
    if (activeId.value) activeMap.value[id] = activeId.value
    else delete activeMap.value[id]
    localStorage.setItem(activeKey, JSON.stringify(activeMap.value))
  }
  function select(id) { activeId.value = id; persist() }
  function create(question = '') {
    const id = `c_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`
    list.value.unshift({ id, title: makeTitle(question), updatedAt: new Date().toISOString(), turns: [] })
    select(id)
    return id
  }
  function remove(id) {
    list.value = list.value.filter((item) => item.id !== id)
    if (activeId.value === id) activeId.value = list.value[0]?.id || ''
    persist()
  }
  function append(turn) {
    let current = list.value.find((item) => item.id === activeId.value)
    if (!current) { create(turn.question); current = list.value[0] }
    current.turns.push({ question: turn.question, answer: turn.answer, citations: turn.citations || [], grounded: turn.grounded !== false, mode: turn.mode || 'qa' })
    if (current.turns.length === 1) current.title = makeTitle(turn.question)
    current.updatedAt = new Date().toISOString()
    list.value = [current, ...list.value.filter((item) => item.id !== current.id)]
    persist()
  }
  watch(() => course.currentId, load, { immediate: true })
  watch(askMode, (value) => localStorage.setItem('sz.ask_mode', value))
  watch(historyCollapsed, (value) => localStorage.setItem('sz.history_collapsed', value ? '1' : '0'))
  watch(historyWidth, (value) => localStorage.setItem('sz.history_width', String(value)))
  return { list, activeId, askMode, historyCollapsed, historyWidth, load, create, select, remove, append }
})
