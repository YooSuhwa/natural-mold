'use client'

import { useMemo } from 'react'
import { useTranslations } from 'next-intl'
import { ListIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { OutlinePayload } from '@/lib/stores/chat-right-rail'

interface Props {
  payload: OutlinePayload
}

interface OutlineHeading {
  level: 1 | 2 | 3 | 4 | 5 | 6
  text: string
  anchor: string
  index: number
}

const HEADING_PATTERN = /^(#{1,6})\s+(.+?)\s*#*\s*$/

function slugify(text: string, index: number): string {
  const slug = text
    .toLowerCase()
    .trim()
    .replace(/[^\w\s-]/g, '')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
  return slug ? `${slug}-${index}` : `heading-${index}`
}

/**
 * Parse markdown content for ATX-style headings (`#`, `##`, ...).
 * Skips heading-like lines inside fenced code blocks (``` or ~~~).
 */
function extractHeadings(content: string): OutlineHeading[] {
  if (!content) return []

  const lines = content.split('\n')
  const headings: OutlineHeading[] = []
  let inFence = false
  let fenceMarker: string | null = null

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    const fenceMatch = line.match(/^(\s{0,3})(```+|~~~+)/)
    if (fenceMatch) {
      const marker = fenceMatch[2][0]
      if (!inFence) {
        inFence = true
        fenceMarker = marker
      } else if (fenceMarker === marker) {
        inFence = false
        fenceMarker = null
      }
      continue
    }
    if (inFence) continue

    const match = line.match(HEADING_PATTERN)
    if (!match) continue
    const level = match[1].length as OutlineHeading['level']
    const text = match[2].trim()
    if (!text) continue
    headings.push({
      level,
      text,
      anchor: slugify(text, headings.length),
      index: headings.length,
    })
  }

  return headings
}

export function OutlinePanelContent({ payload }: Props) {
  const t = useTranslations('chat.rightRail')
  const headings = useMemo(() => extractHeadings(payload.content), [payload.content])

  if (headings.length === 0) {
    return (
      <div className="space-y-3">
        <div className="flex items-center gap-2 text-sm font-medium text-foreground">
          <ListIcon className="size-3.5 text-muted-foreground" aria-hidden />
          {t('outline')}
        </div>
        <p className="rounded-md border border-dashed border-border/60 bg-muted/40 p-3 text-xs text-muted-foreground">
          {t('noHeadings')}
        </p>
        <p className="text-[10px] text-muted-foreground/70">message_id: {payload.messageId}</p>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-sm font-medium text-foreground">
        <ListIcon className="size-3.5 text-muted-foreground" aria-hidden />
        {t('outline')}
        <span className="text-xs font-normal text-muted-foreground">({headings.length})</span>
      </div>
      <nav aria-label={t('messageOutline')}>
        <ul className="space-y-0.5">
          {headings.map((h) => (
            <li key={h.anchor}>
              <button
                type="button"
                onClick={() => {
                  // Placeholder — scroll target not yet implemented (P0-1 작업자가 마무리)
                  if (typeof window !== 'undefined') {
                    window.dispatchEvent(
                      new CustomEvent('moldy:outline-jump', {
                        detail: { messageId: payload.messageId, anchor: h.anchor, text: h.text },
                      }),
                    )
                  }
                }}
                className={cn(
                  'block w-full truncate rounded-md px-2 py-1 text-left text-xs text-foreground/80 transition-colors hover:bg-accent hover:text-foreground',
                  h.level === 1 && 'font-semibold',
                  h.level === 2 && 'pl-3',
                  h.level === 3 && 'pl-5 text-muted-foreground',
                  h.level === 4 && 'pl-7 text-muted-foreground',
                  h.level === 5 && 'pl-9 text-muted-foreground',
                  h.level === 6 && 'pl-11 text-muted-foreground',
                )}
                title={h.text}
              >
                {h.text}
              </button>
            </li>
          ))}
        </ul>
      </nav>
      <p className="text-[10px] text-muted-foreground/70">message_id: {payload.messageId}</p>
    </div>
  )
}
