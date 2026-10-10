import { describe, expect, it, vi } from 'vitest'
import { effectScope, nextTick, ref } from 'vue'
import { usePractice } from './usePractice.js'

vi.mock('@/api/client.js', () => ({ apiGet: vi.fn(), apiPost: vi.fn() }))
import { apiGet, apiPost } from '@/api/client.js'
const settle = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); await nextTick() }

describe('practice request isolation', () => {
  it('ignores an old course response and clears the selected attempt on course change', async () => {
    let resolveOld
    apiGet.mockImplementation((path, params) => params.course_id === 'a' ? new Promise((resolve) => { resolveOld = resolve }) : Promise.resolve({ course: 'b' }))
    const course = ref('a'), id = ref('')
    const scope = effectScope()
    let state
    scope.run(() => { state = usePractice({ courseId: () => course.value, attemptId: () => id.value, questionId: () => '', selectAttempt: async (value) => { id.value = value || '' } }) })
    course.value = 'b'; await nextTick(); await settle()
    resolveOld({ course: 'a' }); await settle()
    expect(state.review.value.course).toBe('b')
    id.value = 'old'; await settle()
    course.value = 'c'; await settle()
    expect(id.value).toBe('')
    scope.stop()
  })
  it('reuses the start request key after a lost response', async () => {
    apiGet.mockResolvedValue({ recommendations: [] })
    apiPost.mockRejectedValueOnce(new Error('网络中断')).mockResolvedValueOnce({ id: 'started' })
    const scope = effectScope()
    let state
    scope.run(() => { state = usePractice({ courseId: () => 'math', attemptId: () => '', questionId: () => '', selectAttempt: async () => {} }) })
    await settle()
    await state.start('q')
    await state.start('q')
    const calls = apiPost.mock.calls.slice(-2)
    expect(calls[0][1].request_id).toBe(calls[1][1].request_id)
    expect(state.attempt.value.id).toBe('started')
    scope.stop()
  })
})
