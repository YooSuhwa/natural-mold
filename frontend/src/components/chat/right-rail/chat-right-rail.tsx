'use client'

import { useAtom } from 'jotai'
import { XIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import {
  chatRightRailAtom,
  type RightRailState,
} from '@/lib/stores/chat-right-rail'
import { SubagentPanelContent } from './subagent-panel-content'
import { ToolResultPanelContent } from './tool-result-panel-content'
import { OutlinePanelContent } from './outline-panel-content'

interface Props {
  className?: string
}

const PANEL_WIDTH_CLASS = 'w-[380px]'

export function ChatRightRail({ className }: Props) {
  const [state, setState] = useAtom(chatRightRailAtom)
  const isOpen = state.mode !== 'none'

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
            aria-label="Close panel"
            className="absolute inset-0 bg-background/60 backdrop-blur-sm"
            onClick={() => setState({ mode: 'none' })}
          />
          <div className="absolute inset-y-0 right-0 w-[88vw] max-w-[380px] border-l border-border/60 bg-card shadow-xl">
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
  return (
    <div className={cn('flex h-full flex-col', className)}>
      <header className="flex shrink-0 items-center justify-between border-b border-border/60 px-4 py-3">
        <h2 className="truncate text-sm font-semibold text-foreground">{titleFor(state)}</h2>
        <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label="Close panel">
          <XIcon className="size-4" />
        </Button>
      </header>
      <div className="flex-1 overflow-y-auto px-4 py-4">
        {state.mode === 'subagent' ? (
          <SubagentPanelContent payload={state.subagent} />
        ) : null}
        {state.mode === 'tool-result' ? (
          <ToolResultPanelContent payload={state.toolResult} />
        ) : null}
        {state.mode === 'outline' ? (
          <OutlinePanelContent payload={state.outline} />
        ) : null}
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
