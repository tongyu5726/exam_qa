import { describe, expect, it } from 'vitest'
import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { createMemoryHistory, createRouter } from 'vue-router'
import AgentTaskCard from './AgentTaskCard.vue'
import AgentToolTrace from './AgentToolTrace.vue'

describe('task progress and outcomes', () => {
  it('renders completed steps, previews and scoped question navigation', async () => {
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/question-bank', name: 'question-bank', component: { template: '<div />' } }] })
    const app = createSSRApp(AgentTaskCard, { current: true, task: {
      goal: '生成第三章练习', status: 'completed', spec: { topic: '第三章', count: 1, question_type: 'choice', save_requested: true },
      steps: [{ name: 'retrieve', label: '查找课程证据', done: true }],
      drafts: [{ stem: '题干', answer: '答案', analysis: '依据', options: ['A', 'B'] }],
      artifacts: [{ id: 'q_123', title: '题干' }],
    } })
    app.use(router)
    const html = await renderToString(app)
    for (const text of ['已完成', '查找课程证据', '题干', '打开题目', 'question_id=q_123']) expect(html).toContain(text)
    expect(html).not.toContain('保存为题库草稿')
  })
  it('offers a retry for incomplete tasks and does not claim completion', async () => {
    const html = await renderToString(createSSRApp(AgentTaskCard, { current: true, task: { status: 'failed', spec: {}, issues: ['部分题目缺少依据'] } }))
    expect(html).toContain('继续执行')
    expect(html).toContain('部分题目缺少依据')
    expect(html).not.toContain('已完成')
  })
  it('offers explicit continuation of a restored running checkpoint', async () => {
    const html = await renderToString(createSSRApp(AgentTaskCard, { current: true, resumed: true, task: { status: 'running', spec: {} } }))
    expect(html).toContain('检查点待继续')
    expect(html).toContain('继续执行')
  })
})


it('shows the confirmed paper blueprint and links to the saved paper', async () => {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/question-bank', name: 'question-bank', component: { template: '<div />' } }] })
  const app = createSSRApp(AgentTaskCard, { current: true, task: {
    kind: 'paper_assemble', status: 'completed', spec: { title: '第三章练习卷', count: 2, total_score: 15, save_requested: true,
      rules: [{ question_type: 'choice', count: 1, score: 5, difficulty: 'medium' }, { question_type: 'short_answer', count: 1, score: 10, difficulty: 'medium' }] },
    assembly: { reused_count: 1, generated_count: 1 }, artifacts: [{ type: 'paper', id: 'paper_123', title: '第三章练习卷' }],
  } })
  app.use(router)
  const html = await renderToString(app)
  for (const text of ['组卷任务', '15 分', '每题 5 分', '每题 10 分', '复用 1 题', 'paper_id=paper_123']) expect(html).toContain(text)
  expect(html).not.toContain('保存试卷草稿')
})

it('shows task execution failure without labeling it as RAG fallback', async () => {
  const html = await renderToString(createSSRApp(AgentToolTrace, { hasTask: true, calls: [{ name: 'save_paper_draft', ok: false, error: '复用题目已修改' }] }))
  for (const text of ['1 次工具调用', '保存试卷', '执行失败', '复用题目已修改']) expect(html).toContain(text)
  expect(html).not.toContain('RAG 兼容路径')
})
