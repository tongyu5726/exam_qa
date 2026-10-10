import { ref, watch } from 'vue'
import { apiGet } from '@/api/client.js'
import { historyTurns } from './conversationHistory.js'

export function useConversationRestore(course, conversations, asking) {
  const checkpoint = ref(null)
  const executions = ref([])
  const loading = ref(false)
  const error = ref('')
  const notice = ref('')
  let revision = 0
  watch(() => course.currentId, async (courseId, _, onCleanup) => {
    if (!courseId) return
    const request = new AbortController()
    onCleanup(() => request.abort())
    try {
      const items = await apiGet('/conversations', { course_id: courseId }, request.signal)
      if (!request.signal.aborted) conversations.mergeServer(items, courseId)
    } catch (cause) { if (cause.name !== 'AbortError') error.value = `历史同步失败：${cause.message}` }
  }, { immediate: true })
  watch([() => course.currentId, () => conversations.activeId, () => conversations.list.find((item) => item.id === conversations.activeId)?.serverBacked], async ([courseId, id], _, onCleanup) => {
    const token = ++revision
    checkpoint.value = null; executions.value = []; error.value = ''; notice.value = ''; loading.value = false
    const item = conversations.list.find((entry) => entry.id === id)
    if (!courseId || !id || (!item?.serverBacked && !item?.turns.length)) return
    const request = new AbortController()
    onCleanup(() => request.abort())
    loading.value = true
    try {
      const data = await apiGet(`/conversations/${encodeURIComponent(id)}`, { course_id: courseId }, request.signal)
      if (request.signal.aborted || token !== revision || asking.value) return
      conversations.restoreTurns(id, historyTurns(data.messages), courseId)
      if (data.total_messages > data.messages.length) notice.value = `服务器仅载入最近 ${data.messages.length}/${data.total_messages} 条消息；已有本地历史保留。`
      checkpoint.value = data.task ? { task: data.task, hint: data.resume_hint } : null
      executions.value = data.executions || []
    } catch (cause) { if (cause.name !== 'AbortError' && token === revision) error.value = `检查点恢复失败：${cause.message}` }
    finally { if (token === revision) loading.value = false }
  }, { immediate: true })
  function clear() { revision++; checkpoint.value = null; loading.value = false }
  return { checkpoint, executions, loading, error, notice, clear }
}
