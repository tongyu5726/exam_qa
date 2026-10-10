import { describe, expect, it } from 'vitest'
import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'
import StudyGoalCard from './StudyGoalCard.vue'

const goal = () => ({ kind: 'study_goal', goal: '帮我复习第三章', status: 'waiting_answer', spec: { topic: '第三章', count: 1 }, plan: [{ id: 's1', action: 'self_test', focus: '采样', status: 'waiting_answer', reason: '先诊断' }], criteria: ['提交一轮自测'], notes: [], issues: [], artifacts: [], self_test: { id: 's1', submitted: false, items: [{ stem: '最低采样频率？', options: ['两倍', '一倍', '一半', '任意'] }] } })
describe('interactive study goals', () => {
  it('shows the plan and a choice form without revealing a solution', async () => {
    const html = await renderToString(createSSRApp(StudyGoalCard, { task: goal(), current: true }))
    for (const text of ['先诊断', '等待作答', '提交本轮答案', 'type="radio"', '第三章', '未授权保存']) expect(html).toContain(text)
    expect(html).not.toContain('答案：')
    expect(html).not.toContain('保存本轮自测题')
  })
  it('shows grading and explicit save after submission, without mastery claims', async () => {
    const task = goal()
    task.status = 'completed'
    task.self_test = { ...task.self_test, submitted: true, correct_count: 0, results: [{ selected: 'B', correct: false }], items: [{ ...task.self_test.items[0], answer: '两倍', analysis: '依据原文', citations: [] }] }
    const html = await renderToString(createSSRApp(StudyGoalCard, { task, current: true }))
    for (const text of ['需要复习', '答案：两倍', '依据原文', '保存本轮自测题', '不代表知识掌握程度']) expect(html).toContain(text)
    expect(html).not.toContain('提交本轮答案')
  })
  it('shows observed results, an adaptive followup and its bounded retest count', async () => {
    const task = goal()
    task.assessments = [{ quiz_id: 's0', action: 'retest', focus: '采样条件', reason: '本轮答错下限，补讲后换题复核', correct_count: 0, total_count: 1 }]
    task.retests = 1
    const html = await renderToString(createSSRApp(StudyGoalCard, { task, current: true }))
    for (const text of ['作答反馈与后续安排', '答对 0/1 题', '补讲后换题复核', '已自动追加 1/2 次重测', '本轮答错下限']) expect(html).toContain(text)
    expect(html).not.toContain('答案：')
  })
  it('distinguishes earlier saved questions from a new unsaved round', async () => {
    const task = goal()
    task.artifacts = [{ id: 'old-question' }]
    task.self_test.submitted = true
    task.self_test.saved = false
    task.self_test.results = [{ selected: 'A', correct: true }]
    const html = await renderToString(createSSRApp(StudyGoalCard, { task, current: true }))
    expect(html).toContain('当前轮次尚未保存')
    expect(html).toContain('保存本轮自测题')
    expect(html).not.toContain('本轮自测题已保存至')
  })
})
