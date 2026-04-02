import { describe, it, expect, vi, beforeEach } from "vitest"
import { streamChat } from "@/lib/sse/stream-chat"
import type { SSEEvent } from "@/lib/types"

const API_BASE = "http://localhost:8001"

/**
 * Helper: create a ReadableStream from SSE-formatted text lines.
 */
function createSSEStream(lines: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder()
  const text = lines.join("\n") + "\n"
  return new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(text))
      controller.close()
    },
  })
}

/**
 * Helper: create a ReadableStream that emits chunks one-by-one.
 */
function createChunkedStream(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder()
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk))
      }
      controller.close()
    },
  })
}

/**
 * Collect all events from the async generator.
 */
async function collectEvents(
  conversationId: string,
  content: string,
  signal?: AbortSignal,
): Promise<SSEEvent[]> {
  const events: SSEEvent[] = []
  for await (const event of streamChat(conversationId, content, signal)) {
    events.push(event)
  }
  return events
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("streamChat", () => {
  it("parses message_start event", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        createSSEStream([
          "event: message_start",
          'data: {"message_id":"msg-1"}',
          "",
        ]),
        { status: 200 },
      ),
    )

    const events = await collectEvents("conv-1", "hello")
    expect(events).toHaveLength(1)
    expect(events[0].event).toBe("message_start")
    expect(events[0].data).toEqual({ message_id: "msg-1" })
  })

  it("parses content_delta event", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        createSSEStream([
          "event: content_delta",
          'data: {"content":"Hello "}',
          "",
          "event: content_delta",
          'data: {"content":"world!"}',
          "",
        ]),
        { status: 200 },
      ),
    )

    const events = await collectEvents("conv-1", "hi")
    expect(events).toHaveLength(2)
    expect(events[0].event).toBe("content_delta")
    expect(events[0].data).toEqual({ content: "Hello " })
    expect(events[1].data).toEqual({ content: "world!" })
  })

  it("parses tool_call_start event", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        createSSEStream([
          "event: tool_call_start",
          'data: {"tool_name":"web_search","args":{"query":"test"}}',
          "",
        ]),
        { status: 200 },
      ),
    )

    const events = await collectEvents("conv-1", "search test")
    expect(events).toHaveLength(1)
    expect(events[0].event).toBe("tool_call_start")
    expect(events[0].data).toEqual({
      tool_name: "web_search",
      args: { query: "test" },
    })
  })

  it("parses tool_call_result event", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        createSSEStream([
          "event: tool_call_result",
          'data: {"tool_name":"web_search","result":"Found 10 results"}',
          "",
        ]),
        { status: 200 },
      ),
    )

    const events = await collectEvents("conv-1", "search test")
    expect(events).toHaveLength(1)
    expect(events[0].event).toBe("tool_call_result")
    expect(events[0].data.result).toBe("Found 10 results")
  })

  it("parses message_end event", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        createSSEStream([
          "event: message_end",
          'data: {"total_tokens":150}',
          "",
        ]),
        { status: 200 },
      ),
    )

    const events = await collectEvents("conv-1", "done")
    expect(events).toHaveLength(1)
    expect(events[0].event).toBe("message_end")
    expect(events[0].data.total_tokens).toBe(150)
  })

  it("parses a full conversation stream with multiple event types", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        createSSEStream([
          "event: message_start",
          'data: {"message_id":"msg-1"}',
          "",
          "event: content_delta",
          'data: {"content":"Let me search for that."}',
          "",
          "event: tool_call_start",
          'data: {"tool_name":"web_search","args":{"query":"AI news"}}',
          "",
          "event: tool_call_result",
          'data: {"tool_name":"web_search","result":"Latest AI news..."}',
          "",
          "event: content_delta",
          'data: {"content":"Here are the results."}',
          "",
          "event: message_end",
          'data: {"total_tokens":500}',
          "",
        ]),
        { status: 200 },
      ),
    )

    const events = await collectEvents("conv-1", "search AI news")
    expect(events).toHaveLength(6)
    expect(events.map((e) => e.event)).toEqual([
      "message_start",
      "content_delta",
      "tool_call_start",
      "tool_call_result",
      "content_delta",
      "message_end",
    ])
  })

  it("handles multi-chunk data correctly", async () => {
    // Simulate data arriving in two chunks that split across an event boundary
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        createChunkedStream([
          "event: content_delta\ndata: {\"conte",
          "nt\":\"hello\"}\n\nevent: message_end\ndata: {\"done\":true}\n\n",
        ]),
        { status: 200 },
      ),
    )

    const events = await collectEvents("conv-1", "test")
    expect(events).toHaveLength(2)
    expect(events[0].event).toBe("content_delta")
    expect(events[0].data).toEqual({ content: "hello" })
    expect(events[1].event).toBe("message_end")
  })

  it("skips malformed JSON lines", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        createSSEStream([
          "event: content_delta",
          "data: {invalid json}",
          "",
          "event: content_delta",
          'data: {"content":"valid"}',
          "",
        ]),
        { status: 200 },
      ),
    )

    const events = await collectEvents("conv-1", "test")
    expect(events).toHaveLength(1)
    expect(events[0].data).toEqual({ content: "valid" })
  })

  it("throws on non-ok response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("Internal Server Error", { status: 500 }),
    )

    await expect(collectEvents("conv-1", "test")).rejects.toThrow(
      "Stream failed: 500",
    )
  })

  it("throws when response body is null", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 200 }),
    )

    // Response() with null body in jsdom still has a body,
    // so we mock it explicitly
    const mockResponse = {
      ok: true,
      status: 200,
      body: null,
    } as unknown as Response

    vi.spyOn(globalThis, "fetch").mockResolvedValue(mockResponse)

    await expect(collectEvents("conv-1", "test")).rejects.toThrow(
      "No response body",
    )
  })

  it("sends correct URL, method, and body", async () => {
    let capturedUrl = ""
    let capturedInit: RequestInit | undefined

    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (input, init) => {
        capturedUrl = input as string
        capturedInit = init
        return new Response(createSSEStream([]), { status: 200 })
      },
    )

    await collectEvents("conv-42", "my message")

    expect(capturedUrl).toBe(
      `${API_BASE}/api/conversations/conv-42/messages`,
    )
    expect(capturedInit?.method).toBe("POST")
    expect(capturedInit?.headers).toEqual({
      "Content-Type": "application/json",
    })
    expect(JSON.parse(capturedInit?.body as string)).toEqual({
      content: "my message",
    })
  })

  it("passes abort signal to fetch", async () => {
    let capturedSignal: AbortSignal | undefined

    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (_input, init) => {
        capturedSignal = init?.signal as AbortSignal | undefined
        return new Response(createSSEStream([]), { status: 200 })
      },
    )

    const controller = new AbortController()
    await collectEvents("conv-1", "test", controller.signal)

    expect(capturedSignal).toBe(controller.signal)
  })

  it("handles empty stream gracefully", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(createSSEStream([]), { status: 200 }),
    )

    const events = await collectEvents("conv-1", "test")
    expect(events).toEqual([])
  })

  it("ignores non-event/data lines", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        createSSEStream([
          ": this is a comment",
          "event: content_delta",
          'data: {"content":"hello"}',
          "",
          "retry: 3000",
          "",
        ]),
        { status: 200 },
      ),
    )

    const events = await collectEvents("conv-1", "test")
    expect(events).toHaveLength(1)
    expect(events[0].data).toEqual({ content: "hello" })
  })
})
