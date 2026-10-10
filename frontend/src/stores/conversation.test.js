import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { useCourseStore } from './course.js'
import { useConversationStore } from './conversation.js'

const data = new Map()
vi.stubGlobal('localStorage', { getItem: (key) => data.get(key) ?? null, setItem: (key, value) => data.set(key, value), removeItem: (key) => data.delete(key) })
beforeEach(() => { data.clear(); setActivePinia(createPinia()) })

describe('conversation migration', () => {
  it('persists the submitted and resolved evidence scope without changing it later', () => {
    useCourseStore().setCourse('math')
    const conversations = useConversationStore()
    conversations.create('问题')
    conversations.append({ question: '问题', answer: '答案', scope: { scenario: '考试', as_of: '2026-09-01' }, intent: { scenario: '考试', as_of: '2026-09-01' } })
    const saved = JSON.parse(data.get('sz.conversations.math'))[0].turns[0]
    expect(saved.scope).toEqual({ scenario: '考试', as_of: '2026-09-01' })
    expect(saved.intent.scenario).toBe('考试')
  })
  it('reads existing per-course history and active map without clearing it', async () => {
    data.set('sz.course_id', 'math')
    data.set('sz.conversations.math', JSON.stringify([{ id: 'old', title: '旧会话', updatedAt: '2026-01-01', turns: [{ question: '旧问题', answer: '旧回答' }] }]))
    data.set('sz.active_conversation_id', JSON.stringify({ math: 'old' }))
    data.set('sz.history_collapsed', '1')
    const course = useCourseStore()
    const conversations = useConversationStore()
    expect(conversations.activeId).toBe('old')
    expect(conversations.list[0].turns[0].answer).toBe('旧回答')
    expect(conversations.historyCollapsed).toBe(true)
    course.setCourse('physics')
    await nextTick()
    expect(conversations.list).toEqual([])
    course.setCourse('math')
    await nextTick()
    expect(conversations.activeId).toBe('old')
  })
  it('restores server-only conversations while preserving local task traces and course isolation', () => {
    useCourseStore().setCourse('math')
    const conversations = useConversationStore()
    conversations.mergeServer([{ id: 'server', title: '复习目标', updated_at: '2026-10-01' }], 'math')
    expect(conversations.activeId).toBe('server')
    conversations.restoreTurns('server', [{ question: '复习', answer: '等待作答', agentTask: { id: 'task' }, toolCalls: [{ name: 'build_study_plan' }] }], 'math')
    conversations.restoreTurns('server', [{ question: '复习', answer: '等待作答' }], 'math')
    expect(conversations.list[0].turns[0].agentTask.id).toBe('task')
    expect(conversations.list[0].turns[0].toolCalls).toHaveLength(1)
    conversations.mergeServer([{ id: 'foreign' }], 'physics')
    expect(conversations.list).toHaveLength(1)
  })
  it('recognizes an interrupted first turn when the server already has a checkpoint', () => {
    useCourseStore().setCourse('math')
    const conversations = useConversationStore()
    const id = conversations.create('刚开始的任务')
    expect(conversations.list[0].turns).toEqual([])
    conversations.mergeServer([{ id, title: '服务端任务', updated_at: '2026-10-01' }], 'math')
    expect(conversations.list[0].serverBacked).toBe(true)
    expect(conversations.activeId).toBe(id)
  })
})
