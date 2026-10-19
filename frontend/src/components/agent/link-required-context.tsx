'use client'

import { createContext, useContext, useId, useMemo, type ReactNode } from 'react'
import { useTranslations } from 'next-intl'

import { Checkbox } from '@/components/ui/checkbox'

interface LinkRequiredState {
  readonly required: ReadonlySet<string>
  readonly onToggle: (id: string) => void
}

const LinkRequiredContext = createContext<LinkRequiredState | null>(null)

/**
 * S29 (plan 12.2): the agent editor's "필수" marks per linked tool / MCP tool / skill /
 * subagent. Screens without a provider (agent creation) render no checkbox.
 */
export function AgentLinkRequiredProvider({
  required,
  onToggle,
  children,
}: LinkRequiredState & { readonly children: ReactNode }) {
  const value = useMemo(() => ({ required, onToggle }), [required, onToggle])
  return <LinkRequiredContext.Provider value={value}>{children}</LinkRequiredContext.Provider>
}

/** "필수" checkbox for one selected link; nothing outside the agent editor. */
export function LinkRequiredToggle({ id, name }: { readonly id: string; readonly name: string }) {
  const state = useContext(LinkRequiredContext)
  const t = useTranslations('agent.linkRequired')
  const labelId = useId()
  if (!state) return null
  return (
    <span className="flex shrink-0 items-center gap-1.5" title={t('help')}>
      <Checkbox
        checked={state.required.has(id)}
        onCheckedChange={() => state.onToggle(id)}
        aria-label={t('toggle', { name })}
        aria-describedby={labelId}
      />
      <span id={labelId} className="moldy-ui-caption text-muted-foreground">
        {t('label')}
      </span>
    </span>
  )
}
