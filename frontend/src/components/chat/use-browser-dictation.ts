'use client'

import { useMemo, useState, useSyncExternalStore } from 'react'
import { WebSpeechDictationAdapter, type DictationAdapter } from '@assistant-ui/react'

export type DictationAvailability = 'ready' | 'unsupported' | 'failed'

class MoldyBrowserDictationAdapter implements DictationAdapter {
  readonly #adapter = new WebSpeechDictationAdapter()

  constructor(private readonly onStartFailure: () => void) {}

  listen(): DictationAdapter.Session {
    try {
      const session = this.#adapter.listen()
      let monitoring = true

      const stopMonitoring = () => {
        monitoring = false
      }
      const monitorSession = () => {
        if (!monitoring) return
        if (session.status.type === 'ended') {
          if (session.status.reason === 'error') this.onStartFailure()
          return
        }
        window.setTimeout(monitorSession, 100)
      }
      window.setTimeout(monitorSession, 100)

      return {
        get status() {
          return session.status
        },
        stop: async () => {
          try {
            await session.stop()
          } finally {
            stopMonitoring()
          }
        },
        cancel: () => {
          stopMonitoring()
          session.cancel()
        },
        onSpeechStart: session.onSpeechStart,
        onSpeechEnd: session.onSpeechEnd,
        onSpeech: session.onSpeech,
      }
    } catch (error) {
      this.onStartFailure()
      throw error
    }
  }
}

class DictationFailureController {
  #failed = false
  readonly #listeners = new Set<() => void>()

  readonly getSnapshot = (): boolean => this.#failed

  readonly subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener)
    return () => this.#listeners.delete(listener)
  }

  readonly markFailed = (): void => {
    if (this.#failed) return
    this.#failed = true
    for (const listener of this.#listeners) listener()
  }

  readonly reset = (): void => {
    if (!this.#failed) return
    this.#failed = false
    for (const listener of this.#listeners) listener()
  }
}

function subscribeBrowserSpeechSupport(listener: () => void): () => void {
  queueMicrotask(listener)
  return () => {}
}

function getBrowserSpeechSupport(): boolean {
  return typeof window !== 'undefined' && WebSpeechDictationAdapter.isSupported()
}

function getServerBrowserSpeechSupport(): boolean {
  return false
}

export interface BrowserDictation {
  readonly adapter?: DictationAdapter
  readonly availability: DictationAvailability
  readonly resetFailure: () => void
}

/**
 * Uses the installed assistant-ui Web Speech adapter only when the browser
 * advertises SpeechRecognition. Recognition processing is browser/vendor
 * provided; Moldy does not add a transcription backend or credential flow.
 */
export function useBrowserDictation(): BrowserDictation {
  const [failureController] = useState(() => new DictationFailureController())
  const supported = useSyncExternalStore(
    subscribeBrowserSpeechSupport,
    getBrowserSpeechSupport,
    getServerBrowserSpeechSupport,
  )
  const startFailed = useSyncExternalStore(
    failureController.subscribe,
    failureController.getSnapshot,
    getServerBrowserSpeechSupport,
  )
  const adapter = useMemo<DictationAdapter | undefined>(
    () => (supported ? new MoldyBrowserDictationAdapter(failureController.markFailed) : undefined),
    [failureController, supported],
  )

  return {
    adapter,
    availability: supported ? (startFailed ? 'failed' : 'ready') : 'unsupported',
    resetFailure: failureController.reset,
  }
}
