'use client'

import { useEffect } from 'react'
import { useAtom } from 'jotai'
import { useTranslations } from 'next-intl'
import { XIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { chatRightRailAtom, type RightRailState } from '@/lib/stores/chat-right-rail'
import { SubagentPanelContent } from './subagent-panel-content'
import { ToolResultPanelContent } from './tool-result-panel-content'
import { OutlinePanelContent } from './outline-panel-content'

interface Props {
  className?: string
  conversationId?: string | null
}

const PANEL_WIDTH_CLASS = 'w-[380px]'

function conversationIdForState(state: RightRailState): string | null | undefined {
  if (state.mode === 'subagent') return state.subagent.conversationId
  if (state.mode === 'tool-result') return state.toolResult.conversationId
  if (state.mode === 'outline') return state.outline.conversationId
  return undefined
}

export function ChatRightRail({ className, conversationId }: Props) {
  const t = useTranslations('chat.rightRail')
  const [state, setState] = useAtom(chatRightRailAtom)
  const stateConversationId = conversationIdForState(state)
  const isStaleConversation =
    state.mode !== 'none' &&
    conversationId !== undefined &&
    conversationId !== null &&
    stateConversationId !== undefined &&
    stateConversationId !== null &&
    stateConversationId !== conversationId
  const isOpen = state.mode !== 'none' && !isStaleConversation

  useEffect(() => {
    if (isStaleConversation) {
      setState({ mode: 'none' })
    }
  }, [isStaleConversation, setState])

  return (
    <>
      {/* 데스크톱: inline split */}
      <aside
        className={cn(
          'hidden shrink-0 overflow-hidden bg-muted/30 transition-[width] duration-200 md:block',
          isOpen ? PANEL_WIDTH_CLASS : 'w-0',
          className,
        )}
        aria-hidden={!isOpen}
      >
        {isOpen ? (
          <RailFrame
            state={state}
            className={PANEL_WIDTH_CLASS}
            onClose={() => setState({ mode: 'none' })}
          />
        ) : null}
      </aside>

      {/* 모바일: fixed overlay (sheet 사용 X) */}
      {isOpen ? (
        <div className="fixed inset-0 z-40 md:hidden" role="dialog" aria-modal="true">
          <button
            type="button"
            aria-label={t('closePanel')}
            className="absolute inset-0 bg-background/60 backdrop-blur-sm"
            onClick={() => setState({ mode: 'none' })}
          />
          <div className="moldy-side-panel absolute inset-y-0 right-0 w-[88vw] max-w-[380px]">
            <RailFrame
              state={state}
              className="h-full w-full"
              onClose={() => setState({ mode: 'none' })}
            />
          </div>
        </div>
      ) : null}
    </>
  )
}

interface RailFrameProps {
  state: RightRailState
  className?: string
  onClose: () => void
}

function RailFrame({ state, className, onClose }: RailFrameProps) {
  const t = useTranslations('chat.rightRail')
  return (
    <div className={cn('flex h-full flex-col', className)}>
      <header className="flex shrink-0 items-center justify-between border-b border-border/60 px-4 py-3">
        <h2 className="truncate text-sm font-semibold text-foreground">{titleFor(state)}</h2>
        <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label={t('closePanel')}>
          <XIcon className="size-4" />
        </Button>
      </header>
      <div className="flex-1 overflow-y-auto px-4 py-4">
        {state.mode === 'subagent' ? <SubagentPanelContent payload={state.subagent} /> : null}
        {state.mode === 'tool-result' ? (
          <ToolResultPanelContent payload={state.toolResult} />
        ) : null}
        {state.mode === 'outline' ? <OutlinePanelContent payload={state.outline} /> : null}
      </div>
    </div>
  )
}

function titleFor(state: RightRailState): string {
  if (state.mode === 'subagent') return state.subagent.agentName || 'Sub-agent'
  if (state.mode === 'tool-result') return state.toolResult.toolName
  if (state.mode === 'outline') return 'Outline'
  return ''
}
