/** POST /ask uses SSE frames, including frames split across UTF-8 and network chunks. */
export async function askStream(payload, onEvent, signal) {
  const response = await fetch('/api/v1/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify({ ...payload, stream: true }),
    signal,
  })
  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    throw new Error(error.message || `问答失败（${response.status}）`)
  }
  if (!response.body) throw new Error('浏览器不支持流式响应')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  const consume = (frame) => {
    const raw = frame.split(/\r?\n/).filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trimStart()).join('\n')
    if (raw) onEvent(JSON.parse(raw))
  }
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const frames = buffer.split(/\r?\n\r?\n/)
      buffer = frames.pop() || ''
      for (const frame of frames) consume(frame)
    }
    buffer += decoder.decode()
    if (buffer.trim()) consume(buffer)
  } finally {
    reader.releaseLock()
  }
}
