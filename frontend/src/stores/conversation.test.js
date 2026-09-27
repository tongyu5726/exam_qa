import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { useCourseStore } from './course.js'
import { useConversationStore } from './conversation.js'

const data = new Map()
vi.stubGlobal('localStorage', { getItem: (key) => data.get(key) ?? null, setItem: (key, value) => data.set(key, value), removeItem: (key) => data.delete(key) })
beforeEach(() => { data.clear(); setActivePinia(createPinia()) })

describe('conversation migration', () => {
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
})
