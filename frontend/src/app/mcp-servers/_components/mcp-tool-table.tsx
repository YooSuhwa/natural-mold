'use client'

import { Fragment, useState } from 'react'
import { useTranslations } from 'next-intl'
import { ChevronDown, ChevronRight } from 'lucide-react'

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Checkbox } from '@/components/ui/checkbox'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { formatDisplayDateTime } from '@/lib/utils/display-format'

const STALE_THRESHOLD_MS = 7 * 24 * 60 * 60 * 1000

/**
 * Minimal shape both ``McpTool`` (persisted) and ``McpProbeTool`` (preview)
 * satisfy — lets the same table render results from either source.
 */
interface McpToolLike {
  id?: string
  name: string
  description: string | null
  enabled?: boolean
  input_schema?: Record<string, unknown>
  /** ISO timestamp of the last successful discovery. Persisted tools only —
   *  preview rows (probe results) leave this undefined. */
  last_seen_at?: string | null
}

interface McpToolTableProps {
  tools: McpToolLike[]
  /** Optional toggler — local state in wizard mode. */
  selected?: Set<string>
  onToggle?: (toolName: string) => void
}

function ExpandableDescription({ text }: { text: string | null | undefined }) {
  const t = useTranslations('mcp.toolTable')
  const [expanded, setExpanded] = useState(false)
  if (!text) return <span>—</span>
  return (
    <button
      type="button"
      onClick={() => setExpanded((v) => !v)}
      title={expanded ? t('collapse') : t('expand')}
      className={`block w-full whitespace-normal break-words text-left transition-colors hover:text-foreground ${
        expanded ? '' : 'line-clamp-3'
      }`}
    >
      {text}
    </button>
  )
}

function isStale(lastSeen: string | null | undefined): boolean {
  if (!lastSeen) return true
  const ts = new Date(lastSeen).getTime()
  if (Number.isNaN(ts)) return true
  return Date.now() - ts > STALE_THRESHOLD_MS
}

function isPersistedTool(tool: McpToolLike): boolean {
  // Persisted rows have an id. Preview/probe rows do not.
  return typeof tool.id === 'string' && tool.id.length > 0
}

export function McpToolTable({ tools, selected, onToggle }: McpToolTableProps) {
  const t = useTranslations('mcp.toolTable')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  if (tools.length === 0) {
    return (
      <p className="rounded border border-dashed p-3 text-center text-xs text-muted-foreground">
        {t('empty')}
      </p>
    )
  }

  function toggleSchema(rowKey: string) {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(rowKey)) next.delete(rowKey)
      else next.add(rowKey)
      return next
    })
  }

  return (
    <div className="w-full overflow-hidden rounded-lg border">
      <Table className="w-full table-fixed">
        <TableHeader>
          <TableRow>
            {onToggle && <TableHead className="w-8" />}
            <TableHead className="w-1/3">{t('columns.tool')}</TableHead>
            <TableHead>{t('columns.description')}</TableHead>
            <TableHead className="w-20 text-right">{t('columns.schema')}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {tools.map((tool) => {
            const rowKey = tool.id ?? tool.name
            const persisted = isPersistedTool(tool)
            const disabled = persisted && tool.enabled === false
            const stale = persisted && isStale(tool.last_seen_at)
            const showSchema = expanded.has(rowKey)
            const hasSchema = !!tool.input_schema && Object.keys(tool.input_schema).length > 0
            return (
              <Fragment key={rowKey}>
                <TableRow className={cn(disabled && 'opacity-60')}>
                  {onToggle && (
                    <TableCell>
                      <Checkbox
                        checked={selected?.has(tool.name) ?? tool.enabled}
                        onCheckedChange={() => onToggle(tool.name)}
                      />
                    </TableCell>
                  )}
                  <TableCell className="break-words font-medium">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span>{tool.name}</span>
                      {disabled ? (
                        <span className="rounded-full bg-muted px-1.5 py-0.5 moldy-ui-micro font-medium text-muted-foreground">
                          {t('disabled')}
                        </span>
                      ) : null}
                      {stale ? (
                        <span
                          className="rounded-full bg-status-warn/15 px-1.5 py-0.5 moldy-ui-micro font-medium text-status-warn"
                          title={
                            tool.last_seen_at
                              ? t('lastSeen', {
                                  date: formatDisplayDateTime(tool.last_seen_at),
                                })
                              : t('neverSeen')
                          }
                        >
                          {t('stale')}
                        </span>
                      ) : null}
                    </div>
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    <ExpandableDescription text={tool.description} />
                  </TableCell>
                  <TableCell className="text-right">
                    {hasSchema ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 px-2 text-xs"
                        onClick={() => toggleSchema(rowKey)}
                      >
                        {showSchema ? (
                          <ChevronDown className="size-3" />
                        ) : (
                          <ChevronRight className="size-3" />
                        )}
                        {t('columns.schema')}
                      </Button>
                    ) : (
                      <span className="moldy-ui-micro text-muted-foreground">—</span>
                    )}
                  </TableCell>
                </TableRow>
                {showSchema && hasSchema ? (
                  <TableRow>
                    <TableCell colSpan={onToggle ? 4 : 3} className="bg-muted/30 p-0">
                      <pre className="max-h-64 overflow-auto p-3 font-mono moldy-ui-caption leading-relaxed text-foreground/80">
                        {JSON.stringify(tool.input_schema, null, 2)}
                      </pre>
                    </TableCell>
                  </TableRow>
                ) : null}
              </Fragment>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}
