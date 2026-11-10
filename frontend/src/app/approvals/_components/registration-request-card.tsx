'use client'

import { useState } from 'react'
import { useFormatter, useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { ApiError } from '@/lib/api/client'
import { useDecideApproval } from '@/lib/hooks/use-approvals'
import type { ApprovalView } from '@/lib/types/approval'

const NOTE_MAX = 200
const REASONS = ['new', 'changed', 'tools'] as const
const CHANGES = ['new', 'changed'] as const

interface CardTool {
  key: string
  name: string
  description: string | null
  readOnly: boolean
  change: (typeof CHANGES)[number] | null
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' ? (value as Record<string, unknown>) : {}
}

function texts(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string')
    : []
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[]): T | null {
  return allowed.find((item) => item === value) ?? null
}

function cardTools(value: unknown): CardTool[] {
  if (!Array.isArray(value)) return []
  const tools: CardTool[] = []
  for (const [index, item] of value.entries()) {
    if (item === null || typeof item !== 'object') continue
    const row = record(item)
    const name = text(row.name)
    if (!name) continue
    tools.push({
      key: text(row.id) ?? `${index}-${name}`,
      name,
      description: text(row.description),
      readOnly: row.read_only === true,
      change: oneOf(row.change, CHANGES),
    })
  }
  return tools
}

/**
 * A tool or MCP server waiting for the administrators (tool-governance-plan 3.7). Everything
 * on the card came from the registrant or the remote server, so it is rendered as plain
 * characters: no links, no markup.
 */
export function RegistrationRequestCard({ request }: { request: ApprovalView }) {
  const t = useTranslations('approvals')
  const tr = useTranslations('approvals.registration')
  const format = useFormatter()
  const decide = useDecideApproval()
  const [note, setNote] = useState('')
  const pending = request.status === 'pending'
  const args = record(request.actions[0]?.args)
  const reason = oneOf(args.reason, REASONS)
  const tools = cardTools(args.tools)
  const command = [text(args.command), ...texts(args.args)].filter(Boolean).join(' ')
  const credential = text(args.credential)
  const headerKeys = texts(args.header_keys)
  const envKeys = texts(args.env_keys)
  const fields: [string, string | null][] = [
    [tr('registrant'), text(args.registrant) ?? tr('unknownRegistrant')],
    [tr('transport'), text(args.transport)],
    [tr('url'), text(args.url)],
    [tr('command'), command || null],
    [tr('definition'), text(args.definition_key)],
    [tr('toolDescription'), text(args.description)],
    [
      tr('credential'),
      credential
        ? `${credential} · ${tr(args.credential_mode === 'per_user' ? 'credentialPerUser' : 'credentialShared')}`
        : tr('noCredential'),
    ],
    [tr('headerKeys'), headerKeys.length ? headerKeys.join(', ') : null],
    [tr('envKeys'), envKeys.length ? envKeys.join(', ') : null],
  ]

  const submit = (type: 'approve' | 'reject') =>
    decide.mutate(
      { id: request.id, decisions: [{ type, message: note.trim() || undefined }] },
      {
        onSuccess: () => toast.success(tr(type === 'approve' ? 'approved' : 'rejected')),
        onError: (error) => {
          const code = error instanceof ApiError ? error.code : ''
          // 409: the registration changed since this card was drawn; the list refetches.
          toast.error(code === 'approval_stale' ? tr('stale') : t('errors.generic'))
        },
      },
    )

  return (
    <SettingsSectionCard title={request.agent_name || text(args.title) || tr('unnamed')}>
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <Badge variant="outline">{t('source.registration')}</Badge>
        <Badge variant="outline">
          {tr(request.resource_type === 'tool' ? 'type.tool' : 'type.mcp_server')}
        </Badge>
        {reason ? <Badge variant="outline">{tr(`reason.${reason}`)}</Badge> : null}
        <Badge variant={pending ? 'secondary' : 'outline'}>{t(`status.${request.status}`)}</Badge>
        {pending ? (
          <span>{t('expires', { when: format.relativeTime(new Date(request.expires_at)) })}</span>
        ) : request.decided_at ? (
          <span>{format.relativeTime(new Date(request.decided_at))}</span>
        ) : null}
      </div>
      <dl className="space-y-1.5 text-xs">
        {fields.map(([label, value]) =>
          value ? (
            <div key={label} className="flex flex-col gap-x-4 sm:flex-row">
              <dt className="shrink-0 text-muted-foreground sm:w-28">{label}</dt>
              <dd className="min-w-0 whitespace-pre-wrap break-all text-foreground">{value}</dd>
            </div>
          ) : null,
        )}
      </dl>
      {tools.length > 0 ? (
        <div className="mt-3 space-y-1.5">
          <p className="text-xs font-medium">{tr('tools', { count: tools.length })}</p>
          <ul className="max-h-72 space-y-1.5 overflow-auto">
            {tools.map((tool) => (
              <li key={tool.key} className="rounded-lg border bg-background p-2 text-xs">
                <div className="flex flex-wrap items-center gap-1.5">
                  <code className="font-medium">{tool.name}</code>
                  {tool.readOnly ? <Badge variant="outline">{tr('readOnly')}</Badge> : null}
                  {tool.change ? (
                    <Badge variant="secondary">{tr(`change.${tool.change}`)}</Badge>
                  ) : null}
                </div>
                {tool.description ? (
                  <p className="mt-1 whitespace-pre-wrap break-words text-muted-foreground">
                    {tool.description}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {pending ? (
        <div className="mt-4 space-y-2">
          <Textarea
            aria-label={tr('noteLabel')}
            placeholder={tr('notePlaceholder')}
            maxLength={NOTE_MAX}
            rows={2}
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
          <div className="flex flex-wrap justify-end gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={decide.isPending}
              onClick={() => submit('reject')}
            >
              {t('decision.reject')}
            </Button>
            <Button size="sm" disabled={decide.isPending} onClick={() => submit('approve')}>
              {t('decision.approve')}
            </Button>
          </div>
        </div>
      ) : (request.status === 'approved' || request.status === 'rejected') && request.reason ? (
        <p className="mt-3 whitespace-pre-wrap break-words text-xs text-muted-foreground">
          {tr('note', { note: request.reason })}
        </p>
      ) : null}
    </SettingsSectionCard>
  )
}
