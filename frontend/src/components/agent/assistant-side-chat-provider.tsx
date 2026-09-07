'use client'

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from 'react'
import { Provider as JotaiProvider } from 'jotai'
import { useTranslations } from 'next-intl'
import { usePathname } from 'next/navigation'

import { AssistantPanel, type AssistantPanelSession } from './assistant-panel'
import { DialogShell } from '@/components/shared/dialog-shell'
import { useIsMobile } from '@/hooks/use-mobile'
import { useSession } from '@/lib/auth/session'
import type { Message } from '@/lib/types'

export interface AssistantSideChatTarget {
  readonly agentId: string
  readonly agentName: string
}

interface RetainedAssistantSession {
  readonly sessionId: string
  readonly messages: Message[]
}

interface AssistantSideChatContextValue {
  readonly isOpen: boolean
  readonly target: AssistantSideChatTarget | null
  readonly openForAgent: (
    target: AssistantSideChatTarget,
    returnFocusTo?: HTMLElement | null,
  ) => void
  readonly close: () => void
}

const AssistantSideChatContext = createContext<AssistantSideChatContextValue | null>(null)
const AGENT_ROUTE_PATTERN = /^\/agents\/([^/]+)(?:\/|$)/

export function useAssistantSideChat(): AssistantSideChatContextValue {
  const value = useContext(AssistantSideChatContext)
  if (!value) {
    throw new Error('useAssistantSideChat must be used within AssistantSideChatProvider')
  }
  return value
}

export function AssistantSideChatProvider({ children }: { readonly children: ReactNode }) {
  const { data: user } = useSession()
  return (
    <AssistantSideChatProviderForUser key={user?.id ?? 'anonymous'}>
      {children}
    </AssistantSideChatProviderForUser>
  )
}

function AssistantSideChatProviderForUser({ children }: { readonly children: ReactNode }) {
  const pathname = usePathname()
  const [target, setTarget] = useState<AssistantSideChatTarget | null>(null)
  const [visibility, setVisibility] = useState({ isOpen: false, pathname })
  const [sessions, setSessions] = useState<Record<string, RetainedAssistantSession>>({})
  const returnFocusRef = useRef<HTMLElement | null>(null)
  const routeAgentId = pathname.match(AGENT_ROUTE_PATTERN)?.[1]

  if (visibility.pathname !== pathname) {
    const movedToDifferentAgent =
      visibility.isOpen && target && routeAgentId && routeAgentId !== target.agentId
    setVisibility({ isOpen: movedToDifferentAgent ? false : visibility.isOpen, pathname })
  }
  const isOpen = visibility.isOpen

  const openForAgent = useCallback(
    (nextTarget: AssistantSideChatTarget, returnFocusTo?: HTMLElement | null) => {
      returnFocusRef.current = returnFocusTo ?? null
      setSessions((current) => {
        if (current[nextTarget.agentId]) return current
        return {
          ...current,
          [nextTarget.agentId]: { sessionId: crypto.randomUUID(), messages: [] },
        }
      })
      setTarget(nextTarget)
      setVisibility({ isOpen: true, pathname })
    },
    [pathname],
  )

  const close = useCallback(() => {
    setVisibility({ isOpen: false, pathname })
    const returnFocusTo = returnFocusRef.current
    if (returnFocusTo) {
      requestAnimationFrame(() => returnFocusTo.focus())
    }
  }, [pathname])

  const value = useMemo(
    () => ({ isOpen, target, openForAgent, close }),
    [close, isOpen, openForAgent, target],
  )

  return (
    <AssistantSideChatContext.Provider value={value}>
      {children}
      <AssistantSideChat sessions={sessions} setSessions={setSessions} />
    </AssistantSideChatContext.Provider>
  )
}

interface AssistantSideChatProps {
  readonly sessions: Readonly<Record<string, RetainedAssistantSession>>
  readonly setSessions: Dispatch<SetStateAction<Record<string, RetainedAssistantSession>>>
}

function AssistantSideChat({ sessions, setSessions }: AssistantSideChatProps) {
  const { close, isOpen, target } = useAssistantSideChat()
  const isMobile = useIsMobile()
  const t = useTranslations('agent.assistant')
  const retainedSession = target ? sessions[target.agentId] : undefined

  const commitMessages = useCallback(
    (messages: Message[]) => {
      if (!target) return
      setSessions((current) => {
        const currentSession = current[target.agentId]
        if (!currentSession) return current
        return {
          ...current,
          [target.agentId]: {
            ...currentSession,
            messages: [...currentSession.messages, ...messages],
          },
        }
      })
    },
    [setSessions, target],
  )

  const session = useMemo<AssistantPanelSession | null>(() => {
    if (!retainedSession) return null
    return { ...retainedSession, onMessagesCommit: commitMessages }
  }, [commitMessages, retainedSession])

  if (!target || !session) return null

  const panel = (
    <JotaiProvider>
      <AssistantPanel
        key={target.agentId}
        agentId={target.agentId}
        agentName={target.agentName}
        session={session}
        showHeader={!isMobile}
        onClose={close}
      />
    </JotaiProvider>
  )

  if (isMobile) {
    return (
      <DialogShell open={isOpen} onOpenChange={(open) => (open ? undefined : close())} size="lg">
        <DialogShell.Header
          title={t('title')}
          description={t('description', { agentName: target.agentName })}
        />
        <DialogShell.Body className="flex min-h-0 flex-col space-y-0 p-0">
          <div id="assistant-side-chat" className="flex min-h-0 flex-1 flex-col">
            {panel}
          </div>
        </DialogShell.Body>
      </DialogShell>
    )
  }

  return isOpen ? (
    <aside
      id="assistant-side-chat"
      data-testid="assistant-side-chat"
      className="moldy-side-panel absolute inset-y-0 right-0 flex min-h-0 w-96 shrink-0 flex-col overflow-hidden 2xl:static"
    >
      {panel}
    </aside>
  ) : null
}
