import { expect, it } from 'vitest'
import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'
import ChatStream from './ChatStream.vue'

it('renders dialogue normally while keeping course refusal distinct', async () => {
  const html = await renderToString(createSSRApp(ChatStream, { turns: [
    { question: '你是什么ai', answer: '我是溯知', grounded: false, responseKind: 'conversation', mode: 'concept', scope: { scenario: '考试' }, citations: [] },
    { question: '进程是什么', answer: '资料库中未找到相关内容', grounded: false, mode: 'concept', citations: [] },
  ] }))
  const bubbles = html.match(/<div[^>]*class="answer-bubble"[^>]*>/g)
  expect(bubbles).toHaveLength(2)
  expect(bubbles[0]).not.toContain('data-grounded')
  expect(bubbles[1]).toContain('data-grounded="false"')
  expect(html).toContain('交流')
  expect(html).not.toContain('查询范围')
})
