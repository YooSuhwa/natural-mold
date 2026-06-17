'use client'

import { CheckCircle2, Loader2, XCircle } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import type { McpProbeTool } from '@/lib/types/mcp'

import { countMcpToolParameters, type McpProbeState } from './mcp-wizard-form-state'

export function McpWizardProbeBadge({ state }: { readonly state: McpProbeState }) {
  const t = useTranslations('mcp.wizard.probe')
  if (state.kind === 'idle') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-status-warn/15 px-2 py-0.5 text-xs font-medium text-status-warn">
        {t('needed')}
      </span>
    )
  }
  if (state.kind === 'pending') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-status-info/15 px-2 py-0.5 text-xs font-medium text-status-info">
        <Loader2 className="size-3 animate-spin" />
        {t('pending')}
      </span>
    )
  }
  if (state.kind === 'ok') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-status-success/15 px-2 py-0.5 text-xs font-medium text-status-success">
        <CheckCircle2 className="size-3" />
        {t('ok', { count: state.toolCount })}
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-status-danger/15 px-2 py-0.5 text-xs font-medium text-status-danger">
      <XCircle className="size-3" />
      {t('failed')}
    </span>
  )
}

export function McpWizardProbeSection({
  probeState,
  tools,
  onRetry,
}: {
  readonly probeState: McpProbeState
  readonly tools: readonly McpProbeTool[]
  readonly onRetry: () => void
}) {
  const t = useTranslations('mcp.wizard.tools')
  if (probeState.kind === 'pending') {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" /> {t('discovering')}
      </div>
    )
  }
  if (probeState.kind === 'error') {
    return (
      <div className="space-y-3 rounded-md border border-status-danger/40 bg-status-danger/10 p-3 text-sm text-status-danger">
        <p className="font-medium">{t('probeFailed')}</p>
        <p className="text-xs">{probeState.message}</p>
        <Button size="sm" variant="outline" onClick={onRetry}>
          {t('retry')}
        </Button>
      </div>
    )
  }
  if (tools.length === 0) {
    return (
      <p className="rounded border border-dashed border-border/60 p-6 text-center text-xs text-muted-foreground">
        {t('empty')}
      </p>
    )
  }
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>{t('discovered', { total: tools.length })}</span>
        <Button size="sm" variant="ghost" onClick={onRetry}>
          {t('reprobe')}
        </Button>
      </div>
      <div className="space-y-1.5">
        {tools.map((tool) => {
          const paramCount = countMcpToolParameters(tool.input_schema)
          return (
            <div
              key={tool.name}
              className="flex items-start gap-2.5 rounded-md border border-border/60 p-2.5"
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate font-mono text-xs font-medium">{tool.name}</span>
                  <span className="rounded-full bg-muted px-1.5 py-0.5 moldy-ui-micro text-muted-foreground">
                    {t('params', { count: paramCount })}
                  </span>
                </div>
                {tool.description ? (
                  <p className="mt-0.5 line-clamp-2 moldy-ui-caption text-muted-foreground">
                    {tool.description}
                  </p>
                ) : null}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
