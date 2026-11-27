'use client'

import { useEffect, useRef, useSyncExternalStore } from 'react'
import type { AssistantRuntime } from '@assistant-ui/react'
import { replaceEqualDeep } from '@tanstack/react-query'
import { ApiError } from '@/lib/api/errors'
import {
  isMoldyBranchSwitchedEvent,
  MOLDY_BRANCH_SWITCHED_EVENT,
} from '@/lib/chat/langgraph-runtime/branch-switch-events'
import { reportClientWarning } from '@/lib/logging/client-logger'
import { SupersededHistoryRead, type createAgUiChatSession } from './chat-history'
import type { MoldyAgUiAgent } from './moldy-agent'

interface HistoryOptions {
  readonly runtime: AssistantRuntime
  readonly session: ReturnType<typeof createAgUiChatSession>
  readonly conversationId: string
  readonly agent: MoldyAgUiAgent
  readonly readOnly?: boolean
  readonly serverActiveRunId?: string | null
  readonly onSettled?: () => void
}

/** The official history adapter owns initial import. Later reads reconcile server IDs and approvals. */
export function useDurableAgUiHistory({
  runtime,
  session,
  conversationId,
  agent,
  readOnly = false,
  serverActiveRunId,
  onSettled,
}: HistoryOptions): boolean {
  const thread = runtime.thread
  const isRunning = useSyncExternalStore(
    thread.subscribe,
    () => thread.getState().isRunning,
    () => false,
  )
  const hasActiveRun = useSyncExternalStore(
    session.subscribe,
    () => session.getSnapshot().activeRunId !== null,
    () => false,
  )
  const settled = useRef(onSettled)
  const refresh = useRef<(() => void) | null>(null)
  const previousServerRun = useRef(serverActiveRunId)
  useEffect(() => {
    settled.current = onSettled
  }, [onSettled])

  useEffect(() => {
    let mounted = true
    let scheduled = false
    let needsRead = false
    let wasRunning = false
    let attaching = false
    let displayedRunId: string | null = null
    let refreshRejected = false
    let denialCleared = false
    let lastReset = session.getSnapshot().reconcileSequence
    let lastTerminal = session.getSnapshot().terminalSequence
    let notifiedTerminal = lastTerminal
    let read: symbol | null = null
    let readScope = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    let retryDelay = 200
    let accessTimer: ReturnType<typeof setTimeout> | undefined

    function schedule() {
      if (scheduled || !mounted) return
      scheduled = true
      queueMicrotask(() => {
        scheduled = false
        if (mounted) reconcile()
      })
    }

    function retry() {
      needsRead = true
      clearTimeout(timer)
      timer = setTimeout(() => {
        timer = undefined
        schedule()
      }, retryDelay)
      retryDelay = Math.min(5000, retryDelay * 2)
    }

    function requestFreshHistory() {
      clearTimeout(timer)
      timer = undefined
      retryDelay = 200
      needsRead = true
      schedule()
    }

    function refreshIfIdle() {
      if (!read) requestFreshHistory()
    }

    async function importCurrentBranch(recheckAccess = false) {
      const previous = recheckAccess ? session.getSnapshot().snapshot : null
      const request = Symbol()
      const signal = readScope.signal
      read = request
      needsRead = false
      try {
        const repo = await session.refreshRepository({ signal })
        if (
          read !== request ||
          signal.aborted ||
          !mounted ||
          thread.getState().isRunning ||
          agent.isRunning
        )
          return
        const state = session.getSnapshot()
        // The terminal frame can be delivered while the read model still shows that run as active.
        if (
          state.activeRunId &&
          state.activeRunId === state.terminalRunId &&
          lastTerminal > notifiedTerminal
        ) {
          retry()
          return
        }
        refreshRejected = false
        // A permission check can return the same pause. Preserve the user's local approval draft.
        if (!previous || replaceEqualDeep(previous, state.snapshot) !== previous)
          thread.import(repo)
        const disconnectedRunSettled = displayedRunId && state.activeRunId !== displayedRunId
        if (lastTerminal > notifiedTerminal || disconnectedRunSettled) {
          displayedRunId = null
          notifiedTerminal = lastTerminal
          settled.current?.()
        }
      } catch (error) {
        if (
          read !== request ||
          signal.aborted ||
          !mounted ||
          error instanceof SupersededHistoryRead
        )
          return
        if (
          error instanceof ApiError &&
          error.status >= 400 &&
          error.status < 500 &&
          error.status !== 429
        ) {
          refreshRejected = true
          reportClientWarning('AgUiHistory', 'Unable to read the selected conversation.')
          return
        }
        if (!recheckAccess) retry()
      } finally {
        if (read === request) {
          read = null
          schedule()
        }
      }
    }

    async function attach(publicRunId: string) {
      attaching = true
      displayedRunId = publicRunId
      const sequence = session.getSnapshot().terminalSequence
      const parentId = thread.getState().messages.at(-1)?.id ?? null
      try {
        // HttpAgent.connect consumes this selection rather than the SDK's synthetic resume ID.
        await thread.resumeRun({
          parentId,
          runConfig: { custom: { moldy: { attachRunId: publicRunId } } },
        })
      } catch {
        // A truncated response follows the same snapshot recovery as a clean EOF before RUN_FINISHED.
      }
      if (!mounted) return
      attaching = false
      const state = session.getSnapshot()
      if (!state.readDenied && state.attachBlockedRunId !== publicRunId) {
        if (sequence === state.terminalSequence) retry()
        else {
          needsRead = true
          retryDelay = 200
        }
      }
      schedule()
    }

    function legacyApprovalVisible() {
      const state = session.getSnapshot()
      const native = thread.getState()
      // The snapshot parser only grants this marker to a current interrupt whose child and run match.
      return (
        !readOnly &&
        document.visibilityState === 'visible' &&
        !state.readDenied &&
        !state.activeRunId &&
        !native.isLoading &&
        !native.isRunning &&
        !agent.isRunning &&
        !!state.snapshot?.interruptRunId &&
        state.interrupts.some((slot) => slot.metadata?.moldy?.legacyInterruptAccess === true)
      )
    }

    function watchLegacyApproval() {
      if (!legacyApprovalVisible()) {
        clearTimeout(accessTimer)
        accessTimer = undefined
      } else if (accessTimer === undefined) {
        accessTimer = setTimeout(() => {
          accessTimer = undefined
          if (mounted && !read && !needsRead && !attaching && legacyApprovalVisible()) {
            void importCurrentBranch(true)
          } else schedule()
        }, 15_000)
      }
    }

    function reconcile() {
      if (agent.documentDetached) return
      watchLegacyApproval()
      const state = session.getSnapshot()
      const native = thread.getState()
      if (state.readDenied && !denialCleared) {
        if (agent.isRunning) agent.abortRun()
        if (native.isRunning) return
        denialCleared = true
        wasRunning = false
        displayedRunId = null
        needsRead = false
        clearTimeout(timer)
        timer = undefined
        thread.import({ headId: null, messages: [] })
        return
      }
      if (!state.readDenied) denialCleared = false
      if (state.reconcileSequence !== lastReset) {
        lastReset = state.reconcileSequence
        needsRead = true
      }
      if (state.terminalSequence !== lastTerminal) {
        lastTerminal = state.terminalSequence
        needsRead = true
      }
      if (native.isRunning || agent.isRunning) {
        wasRunning = true
        displayedRunId = state.activeRunId ?? displayedRunId
        return
      }
      if (wasRunning) {
        wasRunning = false
        needsRead = true
      }
      if (native.isLoading || attaching || read || timer !== undefined) return
      if (needsRead) {
        void importCurrentBranch()
        return
      }
      if (
        !readOnly &&
        !state.readDenied &&
        !refreshRejected &&
        state.activeRunId !== state.attachBlockedRunId &&
        state.activeRunId &&
        state.activeRunId === state.snapshot?.activeRunId
      ) {
        void attach(state.activeRunId)
      }
    }

    function threadChanged() {
      if (thread.getState().isRunning) {
        wasRunning = true
        // A new local submission supersedes reads even when its stream finishes between React renders.
        if (read) {
          session.invalidateHistoryRead()
          read = null
        }
      }
      schedule()
    }
    function branchChanged(event: Event) {
      if (!isMoldyBranchSwitchedEvent(event) || event.detail.conversationId !== conversationId)
        return
      session.invalidateApproval()
      session.invalidateHistoryRead()
      read = null
      if (agent.isRunning) agent.abortRun()
      if (!thread.getState().isLoading) requestFreshHistory()
    }
    function visible() {
      if (document.visibilityState === 'visible') refreshIfIdle()
      else schedule()
    }
    function pageHidden() {
      // Browser navigation does not unmount React before it discards active response readers.
      agent.detachDocument()
      session.cancelHistoryReads()
      readScope.abort()
      read = null
      clearTimeout(timer)
      clearTimeout(accessTimer)
      timer = undefined
      accessTimer = undefined
    }
    function pageShown(event: PageTransitionEvent) {
      if (!event.persisted) return
      agent.restoreDocument()
      if (readScope.signal.aborted) readScope = new AbortController()
      requestFreshHistory()
    }
    refresh.current = () => {
      // A changed server run can predate the response of a GET already in flight.
      // Discard that response and coalesce server transitions into one trailing read.
      session.invalidateHistoryRead()
      if (!thread.getState().isLoading) requestFreshHistory()
    }
    if (readOnly && agent.isRunning) {
      agent.abortRun()
      needsRead = true
    }
    const offSession = session.subscribe(schedule)
    const offThread = thread.subscribe(threadChanged)
    // A lost response can finalize HttpAgent after the last observable assistant-ui state update.
    const agentEvents = agent.subscribe({ onRunFinalized: schedule })
    window.addEventListener(MOLDY_BRANCH_SWITCHED_EVENT, branchChanged)
    window.addEventListener('pagehide', pageHidden)
    window.addEventListener('pageshow', pageShown)
    window.addEventListener('online', refreshIfIdle)
    window.addEventListener('focus', visible)
    document.addEventListener('visibilitychange', visible)
    schedule()
    return () => {
      mounted = false
      refresh.current = null
      readScope.abort()
      clearTimeout(timer)
      clearTimeout(accessTimer)
      offSession()
      offThread()
      agentEvents.unsubscribe()
      window.removeEventListener(MOLDY_BRANCH_SWITCHED_EVENT, branchChanged)
      window.removeEventListener('pagehide', pageHidden)
      window.removeEventListener('pageshow', pageShown)
      window.removeEventListener('online', refreshIfIdle)
      window.removeEventListener('focus', visible)
      document.removeEventListener('visibilitychange', visible)
    }
  }, [session, thread, agent, conversationId, readOnly])

  useEffect(() => {
    if (serverActiveRunId === previousServerRun.current) return
    previousServerRun.current = serverActiveRunId
    refresh.current?.()
  }, [serverActiveRunId])
  return isRunning || (!readOnly && hasActiveRun)
}
