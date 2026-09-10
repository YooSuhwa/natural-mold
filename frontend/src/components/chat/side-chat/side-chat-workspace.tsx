'use client'

import { useCallback, useMemo, useRef, useState, type ReactNode } from 'react'
import { useAtom } from 'jotai'
import { useCreateSideChat } from '@/lib/hooks/use-side-chat'
import { chatRightRailAtom } from '@/lib/stores/chat-right-rail'
import { SideChatContext, type QuoteDelivery, type SideChatContextValue } from './side-chat-context'
import { SelectionActions } from './selection-actions'

export function SideChatWorkspace({
  conversationId,
  title,
  children,
}: {
  readonly conversationId: string | null
  readonly title: string
  readonly children: ReactNode
}) {
  const [sideId, setSideId] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(false)
  const [delivery, setDelivery] = useState<QuoteDelivery | null>(null)
  const nextId = useRef(0)
  const inFlight = useRef<Promise<void> | null>(null)
  const [rail, setRail] = useAtom(chatRightRailAtom)
  const previousRail = useRef(rail)
  const rootRef = useRef<HTMLDivElement>(null)
  const draft: SideChatContextValue['draft'] = useRef(null)
  const { mutateAsync: createSideChat } = useCreateSideChat()

  const show = useCallback(() => {
    if (!conversationId) return
    if (!open) previousRail.current = rail
    setOpen(true)
    setRail({ mode: 'none' })
    if (sideId || inFlight.current) return
    setLoading(true)
    setError(false)
    // Surface failures without losing the selected excerpt; retry reuses the draft.
    inFlight.current = createSideChat(conversationId)
      .then((conversation) => setSideId(conversation.id))
      .catch(() => setError(true))
      .finally(() => {
        setLoading(false)
        inFlight.current = null
      })
  }, [conversationId, createSideChat, setRail, sideId, open, rail])
  const close = useCallback(() => {
    setOpen(false)
    setRail((current) => (current.mode === 'none' ? previousRail.current : current))
  }, [setRail])
  const deliver = useCallback(
    (request: Omit<QuoteDelivery, 'id'>) => {
      setDelivery({ ...request, id: ++nextId.current })
      if (request.target === 'side') show()
    },
    [show],
  )
  const acknowledge = useCallback((id: number) => {
    setDelivery((current) => (current?.id === id ? null : current))
  }, [])
  const value = useMemo(
    () => ({
      draft,
      mainId: conversationId,
      mainTitle: title,
      sideId,
      open,
      loading,
      error,
      delivery,
      show,
      close,
      deliver,
      acknowledge,
    }),
    [
      conversationId,
      title,
      sideId,
      open,
      loading,
      error,
      delivery,
      show,
      close,
      deliver,
      acknowledge,
    ],
  )

  return (
    <SideChatContext.Provider value={value}>
      <div ref={rootRef} className="contents">
        {children}
        <SelectionActions rootRef={rootRef} />
      </div>
    </SideChatContext.Provider>
  )
}
