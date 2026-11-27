// Playwright serializes this function into each new document. All runtime dependencies live inside it.
export function observeCompletedAgUiResponseReads(options: {
  origin: string
  binding: string
  secret: string
  eventTypes: string[]
}) {
  if (window !== window.top || typeof window.fetch !== 'function') return
  const receive: unknown = Reflect.get(window, options.binding)
  if (typeof receive !== 'function') return
  const documentId = crypto.randomUUID()
  const eventTypes = new Set(options.eventTypes)
  let active = true

  function report(kind: string, ids?: { threadId: string; runId: string; physicalRunId: string }) {
    if (!active) return
    const message = { secret: options.secret, document: documentId, kind, ...ids }
    void Promise.resolve(Reflect.apply(receive as CallableFunction, window, [message])).catch(
      () => {},
    )
  }
  report('document')
  window.addEventListener(
    'pagehide',
    () => {
      report('closed')
      active = false
    },
    { once: true },
  )

  function record(value: unknown): value is Record<string, unknown> {
    return typeof value === 'object' && value !== null && !Array.isArray(value)
  }
  function identifier(value: unknown): value is string {
    return typeof value === 'string' && value.length <= 512 && value.trim().length > 0
  }
  function validOutcome(value: unknown): value is { type: 'success' | 'interrupt' | 'cancelled' } {
    if (!record(value)) return false
    // The server emits only the discriminator for cancellation. The host independently verifies the stop receipt.
    if (value.type === 'cancelled') return Object.keys(value).length === 1
    if (value.type === 'success') {
      return (
        value.pendingToolCallIds === undefined ||
        (Array.isArray(value.pendingToolCallIds) && value.pendingToolCallIds.every(identifier))
      )
    }
    return (
      value.type === 'interrupt' &&
      Array.isArray(value.interrupts) &&
      value.interrupts.length > 0 &&
      value.interrupts.every(
        (entry: unknown) =>
          record(entry) &&
          identifier(entry.id) &&
          identifier(entry.reason) &&
          ['subagentRunId', 'message', 'toolCallId', 'expiresAt'].every(
            (key) => entry[key] === undefined || typeof entry[key] === 'string',
          ) &&
          ['metadata', 'responseSchema'].every(
            (key) => entry[key] === undefined || record(entry[key]),
          ),
      )
    )
  }
  function validUsage(value: unknown) {
    const counts = [
      'inputTokens',
      'outputTokens',
      'totalTokens',
      'reasoningTokens',
      'cachedInputTokens',
      'cacheWriteInputTokens',
    ]
    return (
      value === undefined ||
      (Array.isArray(value) &&
        value.every(
          (item: unknown) =>
            record(item) &&
            ['model', 'provider'].every(
              (key) => item[key] === undefined || typeof item[key] === 'string',
            ) &&
            counts.every((key) => {
              const count = item[key]
              return (
                count === undefined ||
                (typeof count === 'number' && Number.isSafeInteger(count) && count >= 0)
              )
            }),
        ))
    )
  }

  function observe(
    response: Response,
    url: URL,
    threadId: string,
    runId: string,
    signal: AbortSignal,
  ) {
    const physicalRunId = response.headers.get('X-Moldy-Run-Id')
    const stream = response.body
    if (
      !active ||
      signal.aborted ||
      response.redirected ||
      response.url !== url.href ||
      response.status !== 200 ||
      response.headers.get('X-Run-Id') !== runId ||
      response.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase() !==
        'text/event-stream' ||
      !identifier(physicalRunId) ||
      !stream ||
      stream.locked ||
      response.bodyUsed
    )
      return
    const ids = { threadId, runId, physicalRunId }
    const decoder = new TextDecoder('utf-8', { fatal: true })
    let pending = ''
    let started = false
    let completedKind: 'eof' | 'cancelled-eof' | undefined
    let invalid = false
    let eof = false
    let acquired = false
    function invalidate() {
      if (invalid || eof) return
      invalid = true
      pending = ''
      report('invalid', ids)
    }
    signal.addEventListener('abort', invalidate, { once: true })

    function frame(source: string) {
      const lines = source.split(/\r\n|\r|\n/)
      const data: string[] = []
      for (const line of lines) {
        if (line.startsWith(':') || line === '') continue
        // Committed Moldy frames have a public SSE cursor. It is irrelevant to run identity.
        if (line.startsWith('id:') && /^id: ?[!-~]{1,512}$/.test(line)) continue
        if (!line.startsWith('data:')) return invalidate()
        data.push(line.slice(5).replace(/^ /, ''))
      }
      if (!data.length) return
      const event: unknown = JSON.parse(data.join('\n'))
      if (
        !record(event) ||
        typeof event.type !== 'string' ||
        !eventTypes.has(event.type) ||
        (event.timestamp !== undefined && !Number.isSafeInteger(event.timestamp)) ||
        (event.metadata !== undefined && !record(event.metadata)) ||
        event.rawEvent === null ||
        completedKind !== undefined ||
        event.type === 'RUN_ERROR'
      )
        return invalidate()
      if (event.type === 'RUN_STARTED') {
        if (
          started ||
          event.threadId !== threadId ||
          event.runId !== runId ||
          (event.protocolVersion !== undefined && event.protocolVersion !== '1.0') ||
          (event.parentRunId !== undefined && !identifier(event.parentRunId)) ||
          event.input !== undefined
        )
          return invalidate()
        started = true
      } else if (!started) {
        invalidate()
      } else if (event.type === 'RUN_FINISHED') {
        if (
          event.threadId !== threadId ||
          event.runId !== runId ||
          event.result === null ||
          !validOutcome(event.outcome) ||
          !validUsage(event.usage)
        )
          return invalidate()
        completedKind = event.outcome.type === 'cancelled' ? 'cancelled-eof' : 'eof'
        // Fix the receipt ordering at terminal delivery. Only a subsequent clean read can attest to EOF.
        if (completedKind === 'cancelled-eof') report('cancelled-terminal', ids)
      }
    }

    function read(result: ReadableStreamReadResult<Uint8Array>) {
      if (invalid || !active || eof) return
      if (signal.aborted) return invalidate()
      try {
        // Bound every fragment before buffering it. Refuse an oversized frame without retaining it.
        const limit = 1024 * 1024
        if (
          !result.done &&
          (!(result.value instanceof Uint8Array) || result.value.byteLength > limit)
        )
          return invalidate()
        pending += result.done ? decoder.decode() : decoder.decode(result.value, { stream: true })
        // The backend emits LF or CRLF SSE. A trailing partial frame cannot attest to completion.
        let end: RegExpExecArray | null
        while (!invalid && (end = /\r\n\r\n|\n\n/.exec(pending))) {
          if (end.index > limit) return invalidate()
          const complete = pending.slice(0, end.index)
          pending = pending.slice(end.index + end[0].length)
          frame(complete)
        }
        if (pending.length > limit) return invalidate()
        if (result.done && !invalid) {
          if (!started || !completedKind || pending !== '') return invalidate()
          eof = true
          signal.removeEventListener('abort', invalidate)
          report(completedKind, ids)
        }
      } catch {
        invalidate()
      }
    }

    // Shadow methods on this HTTP response only. The SDK receives the original stream,
    // reader, promises and byte arrays, and is the only code that consumes their bytes.
    const originalGetReader = stream.getReader
    const originalCancel = stream.cancel
    Object.defineProperty(stream, 'cancel', {
      configurable: true,
      value(this: typeof stream, ...args: unknown[]) {
        invalidate()
        return Reflect.apply(originalCancel, this, args)
      },
    })
    Object.defineProperty(stream, 'getReader', {
      configurable: true,
      value(this: typeof stream, ...args: unknown[]) {
        const reader: ReadableStreamDefaultReader<Uint8Array> = Reflect.apply(
          originalGetReader,
          this,
          args,
        )
        if (this !== stream || acquired || args.length) {
          invalidate()
          return reader
        }
        acquired = true
        const originalRead = reader.read
        const originalReaderCancel = reader.cancel
        const originalRelease = reader.releaseLock
        try {
          Object.defineProperties(reader, {
            read: {
              configurable: true,
              value(this: typeof reader, ...params: unknown[]) {
                const promise = Reflect.apply(originalRead, this, params) as ReturnType<
                  typeof originalRead
                >
                if (this === reader) void promise.then(read, invalidate)
                return promise
              },
            },
            cancel: {
              configurable: true,
              value(this: typeof reader, ...params: unknown[]) {
                invalidate()
                return Reflect.apply(originalReaderCancel, this, params)
              },
            },
            releaseLock: {
              configurable: true,
              value(this: typeof reader, ...params: unknown[]) {
                if (!eof) invalidate()
                return Reflect.apply(originalRelease, this, params)
              },
            },
          })
        } catch {
          invalidate()
        }
        return reader
      },
    })
  }

  const nativeFetch = window.fetch
  window.fetch = function (...args: Parameters<typeof fetch>) {
    const promise = Reflect.apply(nativeFetch, this, args) as ReturnType<typeof fetch>
    try {
      const [input, init] = args
      if (
        !active ||
        (typeof input !== 'string' && !(input instanceof URL)) ||
        init?.method !== 'POST' ||
        init.credentials !== 'include' ||
        typeof init.body !== 'string' ||
        !init.signal ||
        init.signal.aborted
      )
        return promise
      const url = new URL(input, window.location.href)
      const threadId = /^\/api\/conversations\/([^/]+)\/ag-ui\/runs$/.exec(url.pathname)?.[1]
      if (
        url.origin !== options.origin ||
        url.search ||
        url.hash ||
        url.username ||
        url.password ||
        !identifier(threadId)
      )
        return promise
      const command: unknown = JSON.parse(init.body)
      if (
        !record(command) ||
        command.protocolVersion !== '1.0' ||
        command.threadId !== threadId ||
        !identifier(command.runId)
      )
        return promise
      const runId = command.runId
      const signal = init.signal
      void promise
        .then((response) => observe(response, url, threadId, runId, signal))
        .catch(() => {})
    } catch {
      // An unobservable request remains fully subject to the fixture's existing failure checks.
    }
    return promise
  }
}
