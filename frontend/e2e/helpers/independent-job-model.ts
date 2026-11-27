import { randomUUID } from 'node:crypto'
import { once } from 'node:events'
import { createServer } from 'node:http'
import { json } from 'node:stream/consumers'

export async function startIndependentJobModel(reply: string) {
  const apiKey = randomUUID()
  let completions = 0

  const server = createServer(async (request, response) => {
    if (request.method !== 'POST' || request.url !== '/v1/chat/completions') {
      response.writeHead(404).end()
      return
    }
    if (request.headers.authorization !== `Bearer ${apiKey}`) {
      response.writeHead(401).end()
      return
    }

    const body: unknown = await json(request).catch(() => null)
    if (!body || typeof body !== 'object' || !('model' in body) || typeof body.model !== 'string') {
      response.writeHead(400).end()
      return
    }

    const metadata = {
      id: `chatcmpl-${randomUUID()}`,
      created: Math.floor(Date.now() / 1000),
      model: body.model,
    }
    const message = { role: 'assistant', content: reply }
    const usage = { prompt_tokens: 32, completion_tokens: 12, total_tokens: 44 }
    response.once('finish', () => {
      completions += 1
    })
    if (!('stream' in body) || body.stream !== true) {
      response.writeHead(200, { 'Content-Type': 'application/json' }).end(
        JSON.stringify({
          ...metadata,
          object: 'chat.completion',
          choices: [{ index: 0, message, finish_reason: 'stop' }],
          usage,
        }),
      )
      return
    }

    response.writeHead(200, { 'Content-Type': 'text/event-stream' })
    const chunk = { ...metadata, object: 'chat.completion.chunk' }
    const writeFrame = (choices: object[], extra = {}) =>
      response.write(`data: ${JSON.stringify({ ...chunk, choices, ...extra })}\n\n`)
    writeFrame([{ index: 0, delta: { role: 'assistant', content: '' }, finish_reason: null }])
    writeFrame([{ index: 0, delta: { content: reply }, finish_reason: null }])
    writeFrame([{ index: 0, delta: {}, finish_reason: 'stop' }])
    if (
      'stream_options' in body &&
      body.stream_options &&
      typeof body.stream_options === 'object' &&
      'include_usage' in body.stream_options &&
      body.stream_options.include_usage === true
    ) {
      writeFrame([], { usage })
    }
    response.end('data: [DONE]\n\n')
  })

  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const address = server.address()
  if (!address || typeof address === 'string') {
    server.close()
    throw new Error('Independent job model did not bind a loopback port')
  }

  return {
    baseURL: `http://127.0.0.1:${address.port}/v1`,
    apiKey,
    getCompletionCount: () => completions,
    close: async () => {
      if (!server.listening) return
      const closed = once(server, 'close')
      server.close()
      server.closeAllConnections()
      await closed
    },
  }
}
