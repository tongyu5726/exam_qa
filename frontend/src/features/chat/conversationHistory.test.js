import { describe, expect, it } from 'vitest'
import { historyTurns } from './conversationHistory.js'

describe('published conversation recovery', () => {
  it('pairs only published turns and preserves scope and citations', () => {
    const turns = historyTurns([{ role: 'assistant', content: '窗口外旧回答' }, { role: 'user', content: '问题', intent: { scenario: 'exam' } }, { role: 'assistant', content: '回答', citations: [{ page: 1 }], grounded: true }, { role: 'user', content: '尚未完成' }])
    expect(turns).toHaveLength(1)
    expect(turns[0]).toMatchObject({ question: '问题', answer: '回答', citations: [{ page: 1 }], intent: { scenario: 'exam' }, agentTask: null, toolCalls: [] })
  })
})


it('restores ordinary conversation without claiming course grounding', () => {
  const [turn] = historyTurns([
    { role: 'user', content: '你是谁', intent: { task: 'conversation', mode: 'qa' } },
    { role: 'assistant', content: '我是溯知', grounded: false, citations: [] },
  ])
  expect(turn).toMatchObject({ responseKind: 'conversation', grounded: false, toolCalls: [], citations: [] })
})

it('keeps the published run identifier so resumed task results attach to the right turn', () => {
  const [turn] = historyTurns([{ id: 'run_one_user', role: 'user', content: '出题' }, { id: 'run_one_assistant', role: 'assistant', content: '已保存' }])
  expect(turn.executionId).toBe('run_one')
})
