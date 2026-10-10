import { afterEach, describe, expect, it, vi } from 'vitest'
import { effectScope, nextTick, reactive, ref } from 'vue'
import { apiGet } from '@/api/client.js'
import { useConversationRestore } from './useConversationRestore.js'

vi.mock('@/api/client.js', () => ({ apiGet: vi.fn() }))
const scopes = []
afterEach(() => { scopes.forEach((scope) => scope.stop()); scopes.length = 0; vi.clearAllMocks() })
const flush = async () => { await nextTick(); await Promise.resolve(); await nextTick() }
function setup() {
  const course = reactive({ currentId: 'a' })
  const conversations = reactive({ activeId: 'ca', list: [{ id: 'ca', serverBacked: true }, { id: 'cb', serverBacked: true }], mergeServer: vi.fn(), restoreTurns: vi.fn() })
  const scope = effectScope(); scopes.push(scope)
  return { course, conversations, recovery: scope.run(() => useConversationRestore(course, conversations, ref(false))) }
}
describe('scoped checkpoint recovery', () => {
  it('ignores a late response from a previous course', async () => {
    let resolveOld
    apiGet.mockImplementation((path) => path === '/conversations' ? Promise.resolve([]) : path.endsWith('/ca') ? new Promise((resolve) => { resolveOld = resolve }) : Promise.resolve({ task: { id: 'new-task' }, messages: [], total_messages: 0 }))
    const { course, conversations, recovery } = setup()
    course.currentId = 'b'; conversations.activeId = 'cb'
    await flush()
    resolveOld({ task: { id: 'old-task' }, messages: [], total_messages: 0 })
    await flush()
    expect(recovery.checkpoint.value.task.id).toBe('new-task')
    expect(conversations.restoreTurns).toHaveBeenCalledTimes(1)
    expect(conversations.restoreTurns).toHaveBeenCalledWith('cb', [], 'b')
  })
  it('cannot overwrite a new execution after recovery is cleared', async () => {
    let resolveOld
    apiGet.mockImplementation((path) => path === '/conversations' ? Promise.resolve([]) : new Promise((resolve) => { resolveOld = resolve }))
    const { recovery, conversations } = setup()
    recovery.clear()
    resolveOld({ task: { id: 'old' }, messages: [], total_messages: 0 })
    await flush()
    expect(recovery.checkpoint.value).toBeNull()
    expect(conversations.restoreTurns).not.toHaveBeenCalled()
  })
})
