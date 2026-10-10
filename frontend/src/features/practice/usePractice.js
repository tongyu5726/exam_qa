import { ref, shallowRef, watch, onScopeDispose } from 'vue'
import { apiGet, apiPost } from '@/api/client.js'

export function usePractice({ courseId, attemptId, questionId, selectAttempt }) {
  const review = ref(null)
  const attempt = ref(null)
  const busy = shallowRef(false)
  const loading = shallowRef(false)
  const error = shallowRef('')
  let version = 0
  let controller
  const startKeys = new Map()

  async function load() {
    const current = ++version
    controller?.abort()
    controller = new AbortController()
    const signal = controller.signal
    const course = courseId()
    review.value = null; attempt.value = null; error.value = ''; busy.value = false
    loading.value = !!course
    if (!course) return
    try {
      const [data, active] = await Promise.all([
        apiGet('/question-bank/practice-review', { course_id: course }, signal),
        attemptId() ? apiGet(`/question-bank/practice/${encodeURIComponent(attemptId())}`, { course_id: course }, signal) : null,
      ])
      if (current !== version) return
      review.value = data; attempt.value = active
      if (!active && questionId()) await start(questionId())
    } catch (cause) { if (current === version && cause.name !== 'AbortError') error.value = cause.message }
    finally { if (current === version) loading.value = false }
  }

  async function mutate(action) {
    const current = version
    const course = courseId()
    if (!course || busy.value) return
    busy.value = true; error.value = ''
    try {
      const result = await action(course)
      if (current !== version || course !== courseId()) return
      attempt.value = result
      await selectAttempt(result.id)
      const data = await apiGet('/question-bank/practice-review', { course_id: course })
      if (current === version && course === courseId()) review.value = data
    } catch (cause) { if (current === version) error.value = cause.message }
    finally { if (current === version) busy.value = false }
  }

  function start(id, previousAttemptId = null) {
    const key = `${courseId()}:${id}:${previousAttemptId || ''}`
    if (!startKeys.has(key)) startKeys.set(key, crypto.randomUUID())
    return mutate(async (course) => {
      const result = await apiPost('/question-bank/practice', { course_id: course, question_id: id,
        previous_attempt_id: previousAttemptId, request_id: startKeys.get(key) })
      startKeys.delete(key)
      return result
    })
  }
  function submit(payload) {
    const id = attempt.value?.id
    if (!id) return
    return mutate((course) => apiPost(`/question-bank/practice/${encodeURIComponent(id)}/submit`, { course_id: course, ...payload }))
  }
  function assess(correct) {
    const id = attempt.value?.id
    if (!id) return
    return mutate((course) => apiPost(`/question-bank/practice/${encodeURIComponent(id)}/assessment`, { course_id: course, correct }))
  }
  function retry() { if (attempt.value) return start(attempt.value.question_id, attempt.value.id) }
  function openRecommendation(item) {
    if (item.action === 'resume') return selectAttempt(item.attempt_id)
    return start(item.question_id, item.action === 'retry' ? item.attempt_id : null)
  }
  watch([courseId, attemptId, questionId], (values, oldValues) => {
    if (oldValues.length && values[0] !== oldValues[0] && (values[1] || values[2])) {
      ++version; controller?.abort(); attempt.value = null; review.value = null; busy.value = false
      selectAttempt(null)
      return
    }
    load()
  }, { immediate: true })
  onScopeDispose(() => { version++; controller?.abort() })
  return { review, attempt, busy, loading, error, load, start, submit, assess, retry, openRecommendation }
}
