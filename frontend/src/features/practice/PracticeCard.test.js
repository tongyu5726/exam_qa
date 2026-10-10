import { describe, expect, it, vi } from 'vitest'
import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'
import PracticeCard from './PracticeCard.vue'

vi.stubGlobal('window', { location: { origin: 'http://localhost' } })
vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => {}, removeItem: () => {} })
const attempt = { id: 'a', course_id: 'math', status: 'started', grading_method: 'objective',
  question: { stem: '题干', question_type: 'choice', options: ['甲', '乙'] }, evidence_status: 'current' }

describe('practice card', () => {
  it('lets the user choose an option before revealing feedback', async () => {
    const html = await renderToString(createSSRApp(PracticeCard, { attempt }))
    expect(html).toContain('type="radio"')
    expect(html).toContain('提交答案')
    expect(html).not.toContain('参考答案')
    expect(html).not.toContain('自评正确')
  })
  it('shows the snapshot answer and self assessment after text submission', async () => {
    const html = await renderToString(createSSRApp(PracticeCard, { attempt: {
      ...attempt, status: 'submitted', grading_method: 'self', user_answer: '我的回答',
      question: { ...attempt.question, question_type: 'short_answer', answer: '标准答案', analysis: '依据', citations: [] },
      evidence_status: 'stale',
    } }))
    for (const text of ['我的回答', '标准答案', '自评正确', '自评错误', '资料依据已失效或无法核实']) expect(html).toContain(text)
  })
})
