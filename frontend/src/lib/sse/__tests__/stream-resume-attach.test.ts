import type { SSEEvent } from '@/lib/types'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { streamAgUiRunAttach } from '@/lib/ag-ui/chat-run-consumer'
import { streamSSEGetResume } from '../parse-sse'
import { getChatStreamProtocol, streamResumeAttach } from '../stream-resume-attach'

vi.mock('@/lib/ag-ui/chat-run-consumer', () => ({
  streamAgUiRunAttach: vi.fn(),
}))

vi.mock('../parse-sse', () => ({
  streamSSEGetResume: vi.fn(),
}))

async function collect(stream: AsyncGenerator<SSEEvent>): Promise<SSEEvent[]> {
  const events: SSEEvent[] = []
  for await (const event of stream) events.push(event)
  return events
}

function singleEventStream(event: SSEEvent): AsyncGenerator<SSEEvent> {
  return (async function* () {
    yield event
  })()
}

describe('streamResumeAttach', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    delete process.env.NEXT_PUBLIC_CHAT_STREAM_PROTOCOL
  })

  it('uses the existing Moldy SSE resume stream by default', async () => {
    const event: SSEEvent = { event: 'content_delta', data: { delta: 'moldy' }, id: 'moldy-1' }
    vi.mocked(streamSSEGetResume).mockReturnValue(singleEventStream(event))

    await expect(collect(streamResumeAttach('conversation-1', 'run-1', 'last-1'))).resolves.toEqual(
      [event],
    )

    expect(getChatStreamProtocol()).toBe('moldy_sse')
    expect(streamSSEGetResume).toHaveBeenCalledWith(
      '/api/conversations/conversation-1/runs/run-1/stream',
      undefined,
      {
        runId: 'run-1',
        lastEventId: 'last-1',
        onMode: undefined,
        defaultEvent: 'content_delta',
      },
    )
    expect(streamAgUiRunAttach).not.toHaveBeenCalled()
  })

  it('delegates resume attach to the AG-UI adapter when enabled', async () => {
    process.env.NEXT_PUBLIC_CHAT_STREAM_PROTOCOL = 'ag_ui'
    const event: SSEEvent = { event: 'content_delta', data: { delta: 'ag-ui' }, id: 'ag-1' }
    vi.mocked(streamAgUiRunAttach).mockReturnValue(singleEventStream(event))
    const controller = new AbortController()
    const onMode = vi.fn()

    await expect(
      collect(streamResumeAttach('conversation-1', 'run-1', 'last-1', controller.signal, onMode)),
    ).resolves.toEqual([event])

    expect(getChatStreamProtocol()).toBe('ag_ui')
    expect(streamAgUiRunAttach).toHaveBeenCalledWith(
      'conversation-1',
      'run-1',
      'last-1',
      controller.signal,
      onMode,
    )
    expect(streamSSEGetResume).not.toHaveBeenCalled()
  })
})
