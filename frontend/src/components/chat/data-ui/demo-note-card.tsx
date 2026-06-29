'use client'

import { SparklesIcon } from 'lucide-react'

export interface DemoNoteCardProps {
  readonly text: string
}

/**
 * Phase 1 generative-UI demo component. Renders a typed ``demo_note`` payload as
 * a labeled box, proving the end-to-end pipeline (backend emit → custom SSE →
 * ingestion → registry → data part → render). Real components (DataTable/Chart/
 * Stats/Terminal) are added in Phase 2.
 */
export function DemoNoteCard({ text }: DemoNoteCardProps) {
  return (
    <div
      className="moldy-chat-card my-2 flex max-w-xl items-start gap-2 px-3 py-3"
      data-testid="data-ui-demo-note"
    >
      <SparklesIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
      <p className="text-sm leading-relaxed text-foreground">{text}</p>
    </div>
  )
}
