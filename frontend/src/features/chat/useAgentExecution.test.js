import { afterEach, expect, it, vi } from 'vitest'
import { effectScope, nextTick, reactive, ref } from 'vue'
import { apiGet, apiPost } from '@/api/client.js'
import { useAgentExecution } from './useAgentExecution.js'

vi.mock('@/api/client.js', () => ({ apiGet: vi.fn(), apiPost: vi.fn() }))
const scopes = []
afterEach(() => { scopes.forEach((scope) => scope.stop()); scopes.length = 0; vi.clearAllMocks() })
function setup() {
  apiGet.mockResolvedValue(['agent', 'save_question_drafts'])
  const course = reactive({ currentId: 'course' })
  const conversations = reactive({ activeId: 'conv', list: [{ id: 'conv' }] })
  const recovery = { executions: ref([]) }
  const result = vi.fn()
  const error = vi.fn()
  const scope = effectScope(); scopes.push(scope)
  return { course, conversations, recovery, result, error, execution: scope.run(() => useAgentExecution(course, conversations, recovery, result, error)) }
}

it('restores server breakpoints and merges actual live events without starting execution', async () => {
  const { execution, recovery } = setup()
  recovery.executions.value = [{ id: 'r', status: 'paused', events: [], breakpoints: ['save_question_drafts'] }]
  await nextTick()
  expect(execution.breakpoints.value).toEqual(['save_question_drafts'])
  expect(execution.blocked.value).toBe(true)
  execution.accept({ type: 'node', data: { id: 'n', status: 'completed' } })
  expect(execution.run.value.events).toHaveLength(1)
  expect(apiPost).not.toHaveBeenCalled()
})

it('sends step against the same run and ignores a late response after switching course', async () => {
  const { execution, course, result } = setup()
  execution.accept({ type: 'execution', data: { id: 'r', status: 'paused', events: [] } })
  let resolve
  apiPost.mockImplementation(() => new Promise((done) => { resolve = done }))
  const request = execution.control('step')
  expect(execution.controlling.value).toBe(true)
  expect(apiPost).toHaveBeenCalledWith('/agent/runs/r/control', { course_id: 'course', action: 'step', breakpoints: [] })
  course.currentId = 'other'
  await nextTick()
  resolve({ answer: '旧课程内容', execution: { id: 'r', status: 'completed' } })
  await request
  expect(execution.run.value).toBeNull()
  expect(result).not.toHaveBeenCalled()
  expect(execution.controlling.value).toBe(false)
})

it('continues a paused run, publishes the result once and unblocks new questions', async () => {
  const { execution, result } = setup()
  execution.accept({ type: 'execution', data: { id: 'r', status: 'paused', events: [] } })
  const data = { answer: '已完成', execution: { id: 'r', status: 'completed', events: [] } }
  apiPost.mockResolvedValue(data)
  await execution.control('resume')
  expect(result).toHaveBeenCalledExactlyOnceWith(data)
  expect(execution.blocked.value).toBe(false)
})
