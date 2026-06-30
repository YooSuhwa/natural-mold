'use client'

import { cn } from '@/lib/utils'

export interface TerminalCardProps {
  lines: string[] | string
  exitCode?: number
  command?: string
}

/**
 * Phase 2 generative-UI component: renders a typed ``terminal`` payload as a mono
 * output block with an optional command + exit-code header. Output is rendered as
 * text only (R2 security: never raw HTML), mirroring the code-tool-ui CodeBlock.
 */
export function TerminalCard({ lines, exitCode, command }: TerminalCardProps) {
  const text = Array.isArray(lines) ? lines.join('\n') : lines
  const hasExit = typeof exitCode === 'number'

  return (
    <div
      className="my-2 max-w-2xl overflow-hidden rounded-lg border border-border bg-card"
      data-testid="data-ui-terminal"
    >
      {command || hasExit ? (
        <div className="flex items-center justify-between gap-2 border-b border-border bg-muted/40 px-3 py-1.5">
          {command ? (
            <span className="truncate font-mono text-xs text-muted-foreground">$ {command}</span>
          ) : (
            <span />
          )}
          {hasExit ? (
            <span
              className={cn(
                'shrink-0 rounded px-1.5 py-0.5 font-mono text-xs tabular-nums',
                exitCode === 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-destructive',
              )}
            >
              exit {exitCode}
            </span>
          ) : null}
        </div>
      ) : null}
      <pre className="overflow-x-auto whitespace-pre-wrap break-words p-3 font-mono text-xs leading-relaxed text-foreground">
        {text}
      </pre>
    </div>
  )
}
