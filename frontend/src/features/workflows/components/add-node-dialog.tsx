'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'

import { DialogShell } from '@/components/shared/dialog-shell'
import { Input } from '@/components/ui/input'
import { useAgents } from '@/lib/hooks/use-agents'

import type { AddableType } from '../lib/editor-commands'
import { ADDABLE_KINDS, NODE_ICONS } from '../lib/node-catalog'

export type AddChoice = { type: AddableType; agentId?: string; label?: string }

/** Search node kinds and the agents the person can use; one list, keyboard friendly. */
export function AddNodeDialog({
  open,
  onOpenChange,
  onChoose,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onChoose: (choice: AddChoice) => void
}) {
  return open ? (
    <AddNodeDialogInner open={open} onOpenChange={onOpenChange} onChoose={onChoose} />
  ) : null
}

function AddNodeDialogInner({
  open,
  onOpenChange,
  onChoose,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onChoose: (choice: AddChoice) => void
}) {
  const t = useTranslations('workflows')
  const [query, setQuery] = useState('')
  const agents = useAgents()
  const needle = query.trim().toLowerCase()
  const kinds = ADDABLE_KINDS.filter((kind) =>
    `${t(`kind.${kind}`)} ${t(`kindHint.${kind}`)}`.toLowerCase().includes(needle),
  )
  const agentRows = (agents.data ?? []).filter((agent) =>
    `${agent.name} ${agent.description ?? ''}`.toLowerCase().includes(needle),
  )
  const choose = (choice: AddChoice) => {
    onChoose(choice)
    onOpenChange(false)
  }
  const AgentIcon = NODE_ICONS.agent
  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="md">
      <DialogShell.Header title={t('addDialog.title')} />
      <DialogShell.Body className="flex flex-col gap-4">
        <Input
          autoFocus
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t('addDialog.search')}
          aria-label={t('addDialog.search')}
        />
        <section aria-label={t('addDialog.agents')} className="flex flex-col gap-1">
          <h3 className="text-xs font-medium text-muted-foreground">{t('addDialog.agents')}</h3>
          {agentRows.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t('addDialog.noAgents')}</p>
          ) : (
            agentRows.map((agent) => (
              <button
                key={agent.id}
                type="button"
                className="hover:bg-accent focus-visible:bg-accent disabled:cursor-default flex items-center gap-3 rounded-md px-2 py-2 text-left"
                onClick={() => choose({ type: 'agent', agentId: agent.id, label: agent.name })}
              >
                <AgentIcon className="size-4 shrink-0" aria-hidden />
                <span className="min-w-0">
                  <span className="block truncate text-sm">{agent.name}</span>
                  {agent.description ? (
                    <span className="block truncate text-xs text-muted-foreground">
                      {agent.description}
                    </span>
                  ) : null}
                </span>
              </button>
            ))
          )}
        </section>
        <section aria-label={t('addDialog.kinds')} className="flex flex-col gap-1">
          <h3 className="text-xs font-medium text-muted-foreground">{t('addDialog.kinds')}</h3>
          {kinds.length === 0 && agentRows.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t('addDialog.noResults')}</p>
          ) : null}
          {kinds.map((kind) => {
            const Icon = NODE_ICONS[kind]
            return (
              <button
                key={kind}
                type="button"
                className="hover:bg-accent focus-visible:bg-accent disabled:cursor-default flex items-center gap-3 rounded-md px-2 py-2 text-left"
                onClick={() => choose({ type: kind })}
              >
                <Icon className="size-4 shrink-0" aria-hidden />
                <span className="min-w-0">
                  <span className="block text-sm">{t(`kind.${kind}`)}</span>
                  <span className="block text-xs text-muted-foreground">
                    {t(`kindHint.${kind}`)}
                  </span>
                </span>
              </button>
            )
          })}
        </section>
      </DialogShell.Body>
    </DialogShell>
  )
}
