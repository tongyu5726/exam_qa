export function historyTurns(messages) {
  const turns = []
  let question = null
  for (const message of messages) {
    if (message.role === 'user') question = message
    else if (message.role === 'assistant' && question) {
      const intent = Object.keys(message.intent || {}).length ? message.intent : question.intent || null
      turns.push({ question: question.content, answer: message.content, citations: message.citations || [],
        ...(question.id?.startsWith('run_') && question.id.endsWith('_user') ? { executionId: question.id.slice(0, -5) } : {}),
        responseKind: intent?.task === 'conversation' ? 'conversation' : 'answer',
        grounded: message.grounded !== false, mode: message.mode || question.mode || 'qa',
        intent, scope: intent ? { scenario: intent.scenario, as_of: intent.as_of } : null, agentTask: null, toolCalls: [] })
      question = null
    }
  }
  return turns
}
