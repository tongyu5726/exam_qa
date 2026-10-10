import { computed, onScopeDispose, ref, watch } from 'vue'
import { apiGet, apiPost } from '@/api/client.js'

export function useAgentExecution(course, conversations, recovery, onResult, onError) {
  const run = ref(null)
  const nodes = ref([])
  const breakpoints = ref([])
  const controlling = ref(false)
  const checkpoints = ref([])
  const blocked = computed(() => run.value && !['completed', 'cancelled'].includes(run.value.status))
  let revision = 0
  let pollTimer
  const stopPoll = () => { clearTimeout(pollTimer); pollTimer = undefined }
  const current = (token, courseId, conversationId) => token === revision && course.currentId === courseId && conversations.activeId === conversationId

  apiGet('/agent/nodes').then((data) => { nodes.value = data }).catch(onError)
  watch([() => course.currentId, () => conversations.activeId], () => { revision++; stopPoll(); run.value = null; checkpoints.value = []; controlling.value = false })
  watch(() => recovery.executions.value, (items) => {
    if (!items?.length) return
    run.value = items[0]; breakpoints.value = items[0].breakpoints || []
    if (run.value.status === 'running') poll(revision, course.currentId, conversations.activeId, run.value.id)
  })

  async function poll(token, courseId, conversationId, runId) {
    try {
      const data = await apiGet(`/agent/runs/${encodeURIComponent(runId)}`, { course_id: courseId })
      if (!current(token, courseId, conversationId)) return
      run.value = data
      if (data.status === 'running') pollTimer = setTimeout(() => poll(token, courseId, conversationId, runId), 750)
    } catch (cause) { if (current(token, courseId, conversationId)) onError(cause) }
  }

  function accept(event) {
    if (event.type === 'execution') {
      run.value = event.data
      const item = conversations.list.find((entry) => entry.id === conversations.activeId)
      if (item) item.serverBacked = true
    } else if (event.type === 'node' && run.value) {
      run.value = { ...run.value, events: [...run.value.events, event.data] }
    } else if (event.type === 'done' && event.data?.execution) run.value = event.data.execution
  }

  async function control(action) {
    if (!run.value) return
    const token = revision
    const courseId = course.currentId
    const conversationId = conversations.activeId
    const runId = run.value.id
    const resumes = ['resume', 'step', 'retry'].includes(action)
    if (resumes && controlling.value) return
    if (resumes) { controlling.value = true; pollTimer = setTimeout(() => poll(token, courseId, conversationId, runId), 750) }
    try {
      const data = await apiPost(`/agent/runs/${encodeURIComponent(runId)}/control`, { course_id: courseId, action, breakpoints: breakpoints.value })
      if (!current(token, courseId, conversationId)) return
      if (data.execution) { run.value = data.execution; onResult(data) }
      else {
        run.value = data
        if (action === 'cancel' && data.status === 'cancelled') onResult({ answer: '执行已取消，已完成的保存结果保留。', execution: data })
      }
    } catch (cause) { if (current(token, courseId, conversationId)) onError(cause) }
    finally { if (current(token, courseId, conversationId) && resumes) { stopPoll(); controlling.value = false } }
  }

  async function inspect() {
    if (!run.value) return
    const token = revision
    const courseId = course.currentId
    const conversationId = conversations.activeId
    try {
      const data = await apiGet(`/agent/runs/${encodeURIComponent(run.value.id)}/checkpoints`, { course_id: courseId })
      if (current(token, courseId, conversationId)) checkpoints.value = data
    } catch (cause) { if (current(token, courseId, conversationId)) onError(cause) }
  }
  onScopeDispose(() => { revision++; stopPoll() })
  return { run, nodes, breakpoints, checkpoints, controlling, blocked, accept, control, inspect }
}
