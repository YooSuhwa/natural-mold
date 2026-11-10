'use client'

import { Fragment, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Ban, ChevronDown, ChevronRight } from 'lucide-react'

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
import { ApprovalDefaultControl } from '@/features/tool-approval/components/approval-default-control'
import { McpToolBlocks } from './mcp-tool-blocks'
import type { ToolApprovalToolV1 } from '@/lib/types/runtime-policy'
import type {
  McpToolPendingVersion,
  McpToolReviewDecision,
  McpToolReviewState,
} from '@/lib/types/mcp'

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
  /** D11 review (persisted tools only). */
  review_state?: McpToolReviewState
  pending_version?: McpToolPendingVersion | null
  approval_default?: ToolApprovalToolV1 | null
}

interface McpToolTableProps {
  tools: McpToolLike[]
  /** Optional toggler — local state in wizard mode. */
  selected?: Set<string>
  onToggle?: (toolName: string) => void
  /** Server managers: approve or reject the version waiting for review. */
  onReview?: (toolId: string, decision: McpToolReviewDecision, fingerprint: string) => void
  /** The tool whose decision is being sent (its buttons are disabled). */
  reviewingToolId?: string | null
  /** Server editors: set a tool's own approval default (`null` follows the server's). */
  onApprovalDefault?: (toolId: string, value: ToolApprovalToolV1 | null) => void
  /** Server managers: keep people or teams from one tool of this server. */
  blocksServerId?: string
  /** The company's administrators review this server's tools, not its managers. */
  administratorsReview?: boolean
}

function ReviewBadge({ tool }: { tool: McpToolLike }) {
  const t = useTranslations('mcp.toolTable.review')
  if (tool.review_state === 'pending') {
    return (
      <span
        className="moldy-status-warn moldy-status-soft rounded-full px-1.5 py-0.5 moldy-ui-micro font-medium"
        data-review="pending"
      >
        {t('pending')}
      </span>
    )
  }
  if (tool.review_state === 'rejected') {
    return (
      <span
        className="rounded-full bg-muted px-1.5 py-0.5 moldy-ui-micro font-medium text-muted-foreground"
        data-review="rejected"
      >
        {t('rejected')}
      </span>
    )
  }
  if (tool.pending_version) {
    return (
      <span
        className="moldy-status-warn moldy-status-soft rounded-full px-1.5 py-0.5 moldy-ui-micro font-medium"
        data-review="changed"
      >
        {t('changed')}
      </span>
    )
  }
  return null
}

function ReviewRow({
  tool,
  colSpan,
  onReview,
  busy,
  administratorsReview,
}: {
  tool: McpToolLike & { id: string; pending_version: McpToolPendingVersion }
  colSpan: number
  onReview?: McpToolTableProps['onReview']
  busy: boolean
  administratorsReview: boolean
}) {
  const t = useTranslations('mcp.toolTable.review')
  const pending = tool.pending_version
  const isNew = tool.review_state !== 'approved'
  return (
    <TableRow data-review-row={tool.name}>
      <TableCell colSpan={colSpan} className="bg-muted/30">
        <div className="space-y-2 text-xs">
          <p className="text-muted-foreground">{isNew ? t('newHelp') : t('changedHelp')}</p>
          {!isNew ? (
            <div className="space-y-1">
              <p className="font-medium">{t('newDescription')}</p>
              <p className="whitespace-pre-wrap break-words">{pending.description || '—'}</p>
            </div>
          ) : null}
          <pre className="max-h-48 overflow-auto rounded border bg-background p-2 font-mono moldy-ui-caption">
            {JSON.stringify(
              { input_schema: pending.input_schema, annotations: pending.annotations },
              null,
              2,
            )}
          </pre>
          {onReview ? (
            <div className="flex gap-2">
              <Button
                size="sm"
                className="h-7 px-2 text-xs"
                disabled={busy}
                onClick={() => onReview(tool.id, 'approve', pending.fingerprint)}
              >
                {t('approve')}
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-7 px-2 text-xs"
                disabled={busy}
                onClick={() => onReview(tool.id, 'reject', pending.fingerprint)}
              >
                {t('reject')}
              </Button>
            </div>
          ) : (
            <p className="text-muted-foreground">
              {administratorsReview ? t('administratorsOnly') : t('managersOnly')}
            </p>
          )}
        </div>
      </TableCell>
    </TableRow>
  )
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

export function McpToolTable({
  tools,
  selected,
  onToggle,
  onReview,
  reviewingToolId = null,
  onApprovalDefault,
  blocksServerId,
  administratorsReview = false,
}: McpToolTableProps) {
  const t = useTranslations('mcp.toolTable')
  const ta = useTranslations('toolApproval.default')
  const columns = 3 + (onToggle ? 1 : 0) + (onApprovalDefault ? 1 : 0)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [blocksOpen, setBlocksOpen] = useState<string | null>(null)

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
            {onApprovalDefault && <TableHead className="w-40 text-right">{ta('column')}</TableHead>}
            <TableHead className={cn('text-right', blocksServerId ? 'w-40' : 'w-20')}>
              {t('columns.schema')}
            </TableHead>
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
                      {persisted ? <ReviewBadge tool={tool} /> : null}
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
                  {onApprovalDefault && (
                    <TableCell className="text-right">
                      {tool.id ? (
                        <ApprovalDefaultControl
                          compact
                          id={tool.id}
                          label={ta('toolSetting', { tool: tool.name })}
                          noneLabel={ta('followServer')}
                          value={tool.approval_default}
                          onChange={(value) => onApprovalDefault(tool.id as string, value)}
                        />
                      ) : null}
                    </TableCell>
                  )}
                  <TableCell className="text-right">
                    {blocksServerId && tool.id ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 px-2 text-xs"
                        aria-expanded={blocksOpen === tool.id}
                        aria-label={t('blocks.toggleLabel', { tool: tool.name })}
                        onClick={() =>
                          setBlocksOpen((open) => (open === tool.id ? null : (tool.id ?? null)))
                        }
                      >
                        <Ban className="size-3" />
                        {t('blocks.toggle')}
                      </Button>
                    ) : null}
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
                    ) : blocksServerId && tool.id ? null : (
                      <span className="moldy-ui-micro text-muted-foreground">—</span>
                    )}
                  </TableCell>
                </TableRow>
                {persisted && tool.id && tool.pending_version ? (
                  <ReviewRow
                    tool={{ ...tool, id: tool.id, pending_version: tool.pending_version }}
                    colSpan={columns}
                    onReview={onReview}
                    busy={reviewingToolId === tool.id}
                    administratorsReview={administratorsReview}
                  />
                ) : null}
                {blocksServerId && tool.id && blocksOpen === tool.id ? (
                  <TableRow data-blocks-row={tool.name}>
                    <TableCell colSpan={columns} className="bg-muted/30">
                      <McpToolBlocks
                        serverId={blocksServerId}
                        toolId={tool.id}
                        toolName={tool.name}
                      />
                    </TableCell>
                  </TableRow>
                ) : null}
                {showSchema && hasSchema ? (
                  <TableRow>
                    <TableCell colSpan={columns} className="bg-muted/30 p-0">
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
