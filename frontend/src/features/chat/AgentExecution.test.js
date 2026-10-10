import { expect, it } from 'vitest'
import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'
import AgentExecution from './AgentExecution.vue'

it('shows the durable paused node, one row per attempt and sanitized summaries', async () => {
  const execution = { id: 'run_one', status: 'paused', next_nodes: ['save_question_drafts'], events: [
    { id: 'generate:1', node: 'generate_question_drafts', status: 'running', input: { evidence_count: 2 } },
    { id: 'generate:1', node: 'generate_question_drafts', status: 'completed', duration_ms: 310, output: { draft_count: 3 } },
  ] }
  const html = await renderToString(createSSRApp(AgentExecution, { execution, nodes: ['agent', 'save_question_drafts'], breakpoints: ['save_question_drafts'] }))
  expect(html).toContain('下一节点：保存题目')
  expect(html).toContain('单步执行')
  expect(html).toContain('继续')
  expect(html).toContain('310 ms')
  expect(html.match(/data-status="completed"/g)).toHaveLength(1)
  expect(html).toContain('evidence_count')
  expect(html).toContain('draft_count')
})

it('offers pause for a running node and removes controls after cancellation', async () => {
  const running = await renderToString(createSSRApp(AgentExecution, { execution: { id: 'r', status: 'running', control: 'pause', events: [] } }))
  expect(running).toContain('暂停已请求，当前节点结束后停下')
  expect(running).toContain('取消执行')
  const cancelled = await renderToString(createSSRApp(AgentExecution, { execution: { id: 'r', status: 'cancelled', events: [] } }))
  expect(cancelled).toContain('已取消')
  expect(cancelled).not.toContain('取消执行')
  expect(cancelled).not.toContain('单步执行')
})
