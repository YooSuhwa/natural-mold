import { StrictMode } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useBrowserDictation } from '../use-browser-dictation'

let originalSpeechRecognition: PropertyDescriptor | undefined

class BrowserSpeechRecognition extends EventTarget {
  static latest: BrowserSpeechRecognition | undefined

  continuous = false
  interimResults = false
  lang = ''

  constructor(private readonly startBehavior: 'normal' | 'throw' = 'normal') {
    super()
    BrowserSpeechRecognition.latest = this
  }

  start(): void {
    if (this.startBehavior === 'throw') throw new Error('permission denied')
    this.dispatchEvent(new Event('start'))
  }

  stop(): void {
    this.dispatchEvent(new Event('end'))
  }

  abort(): void {
    this.dispatchEvent(new Event('error'))
  }

  emitResult(transcript: string, isFinal: boolean): void {
    const result = { 0: { confidence: 1, transcript }, isFinal, length: 1 }
    const event = new Event('result')
    Object.defineProperties(event, {
      resultIndex: { value: 0 },
      results: { value: { 0: result, item: () => result, length: 1 } },
    })
    this.dispatchEvent(event)
  }

  emitPermissionError(): void {
    const event = new Event('error')
    Object.defineProperties(event, {
      error: { value: 'not-allowed' },
      message: { value: 'Permission denied' },
    })
    this.dispatchEvent(event)
  }
}

function installSpeechRecognition(startBehavior: 'normal' | 'throw' = 'normal'): void {
  originalSpeechRecognition = Object.getOwnPropertyDescriptor(window, 'SpeechRecognition')
  Object.defineProperty(window, 'SpeechRecognition', {
    configurable: true,
    value: class extends BrowserSpeechRecognition {
      constructor() {
        super(startBehavior)
      }
    },
  })
}

afterEach(() => {
  if (originalSpeechRecognition) {
    Object.defineProperty(window, 'SpeechRecognition', originalSpeechRecognition)
  } else {
    Reflect.deleteProperty(window, 'SpeechRecognition')
  }
  BrowserSpeechRecognition.latest = undefined
  originalSpeechRecognition = undefined
  vi.useRealTimers()
})

describe('useBrowserDictation', () => {
  it('reports unsupported when browser speech recognition is unavailable', async () => {
    originalSpeechRecognition = Object.getOwnPropertyDescriptor(window, 'SpeechRecognition')
    Object.defineProperty(window, 'SpeechRecognition', { configurable: true, value: undefined })

    const { result } = renderHook(() => useBrowserDictation())

    await waitFor(() => expect(result.current.availability).toBe('unsupported'))
    expect(result.current.adapter).toBeUndefined()
  })

  it('forwards partial and final browser speech results through the installed adapter', async () => {
    installSpeechRecognition()
    const { result } = renderHook(() => useBrowserDictation())

    await waitFor(() => expect(result.current.availability).toBe('ready'))
    const session = result.current.adapter?.listen()
    const onSpeech = vi.fn()
    session?.onSpeech(onSpeech)

    BrowserSpeechRecognition.latest?.emitResult('partial', false)
    BrowserSpeechRecognition.latest?.emitResult('final', true)

    expect(onSpeech).toHaveBeenNthCalledWith(1, { isFinal: false, transcript: 'partial' })
    expect(onSpeech).toHaveBeenNthCalledWith(2, { isFinal: true, transcript: 'final' })
  })

  it('reports a browser speech failure after the browser emits an error', async () => {
    vi.useFakeTimers()
    installSpeechRecognition()
    const { result } = renderHook(() => useBrowserDictation())

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(result.current.availability).toBe('ready')

    act(() => {
      result.current.adapter?.listen()
      BrowserSpeechRecognition.latest?.emitPermissionError()
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100)
    })

    expect(result.current.availability).toBe('failed')
  })

  it('reports a synchronous browser speech failure', async () => {
    installSpeechRecognition('throw')
    const { result } = renderHook(() => useBrowserDictation())

    await waitFor(() => expect(result.current.availability).toBe('ready'))
    act(() => {
      try {
        result.current.adapter?.listen()
      } catch {
        // The official Web Speech adapter rethrows a browser start failure.
      }
    })

    expect(result.current.availability).toBe('failed')
  })

  it('retains async speech failure updates after the Strict Mode effect replay', async () => {
    vi.useFakeTimers()
    installSpeechRecognition()
    const { result } = renderHook(() => useBrowserDictation(), {
      wrapper: ({ children }) => <StrictMode>{children}</StrictMode>,
    })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(result.current.availability).toBe('ready')

    act(() => {
      result.current.adapter?.listen()
      BrowserSpeechRecognition.latest?.emitPermissionError()
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100)
    })

    expect(result.current.availability).toBe('failed')
  })
})
