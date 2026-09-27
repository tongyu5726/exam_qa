import { afterEach, describe, expect, it, vi } from 'vitest'
import { askStream } from './askStream.js'

afterEach(() => vi.unstubAllGlobals())

describe('askStream', () => {
  it('decodes SSE events split across UTF-8 and network chunks', async () => {
    const bytes = new TextEncoder().encode('data: {"type":"delta","text":"中文"}\n\ndata: {"type":"done","data":{"grounded":true}}\n\n')
    const split = bytes.indexOf(0xe4) + 1
    const chunks = [bytes.slice(0, split), bytes.slice(split, split + 2), bytes.slice(split + 2)]
    const stream = new ReadableStream({ start(controller) { for (const chunk of chunks) controller.enqueue(chunk); controller.close() } })
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, body: stream })
    vi.stubGlobal('fetch', fetchMock)
    const events = []
    await askStream({ question: '问题', course_id: 'course-a' }, (event) => events.push(event))
    expect(events).toEqual([{ type: 'delta', text: '中文' }, { type: 'done', data: { grounded: true } }])
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({ course_id: 'course-a', stream: true })
  })

  it('surfaces an API error before reading the stream', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 422, json: async () => ({ message: '课程无效' }) }))
    await expect(askStream({ question: 'x' }, () => {})).rejects.toThrow('课程无效')
  })
})
