'use client'

import { useId, useState, type FormEvent } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { XIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import type { DelegatedAccount } from '@/lib/types/grant'

import { DelegationConfirm, DelegationList } from './delegation-confirm'
import { PrincipalPicker } from './principal-picker'
import type { ShareBackend, ShareSubject } from './share-backend'
import {
  delegationsToConfirm,
  personalAccountUses,
  shareErrorMessage,
  type PersonalAccountUse,
} from './share-error'
import { levelLabelKey } from './share-labels'

const PERSONAL_TYPES = new Set(['credential', 'tool', 'mcp_server', 'agent', 'skill'])

interface ShareFormProps {
  backend: ShareBackend
  excludeKeys: ReadonlySet<string>
  /** 10.4: a shared-account tool/MCP server makes recipients call with the owner's account. */
  requiresAccountAck: boolean
}

/** Today as a local `YYYY-MM-DD` (the date input's own calendar, not UTC). */
function localToday(): string {
  const now = new Date()
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

/** End of the picked local day, as an ISO instant. */
function endOfDay(date: string): string {
  return new Date(`${date}T23:59:59`).toISOString()
}

export function ShareForm({ backend, excludeKeys, requiresAccountAck }: ShareFormProps) {
  const t = useTranslations('sharing')
  const levelGroupId = useId()
  const expiryId = useId()
  const ackId = useId()
  const previewId = useId()
  const [subject, setSubject] = useState<ShareSubject | null>(null)
  const [level, setLevel] = useState(backend.levels[0])
  const [expiry, setExpiry] = useState('')
  const [acknowledged, setAcknowledged] = useState(false)
  const [delegations, setDelegations] = useState<DelegatedAccount[] | null>(null)
  const [personal, setPersonal] = useState<PersonalAccountUse[]>([])
  const expiryAllowed = backend.allowsExpiry(level)
  // D17 (E-06): accounts the agent lends, confirmed before the share request is sent.
  const preview = backend.snapshot?.delegations ?? []
  const canSubmit =
    !!subject && !backend.pending && (!requiresAccountAck || acknowledged) && !delegations

  async function submit(confirmDelegation: boolean) {
    if (!subject) return
    try {
      await backend.share({
        principal: subject,
        level,
        expiresAt: expiryAllowed && expiry ? endOfDay(expiry) : null,
        confirmDelegation,
      })
      toast.success(t('toast.shared', { name: subject.displayName }))
      setSubject(null)
      setExpiry('')
      setDelegations(null)
      setPersonal([])
    } catch (error) {
      setPersonal(personalAccountUses(error))
      const pending = delegationsToConfirm(error)
      if (pending && !confirmDelegation) {
        setDelegations(pending)
        return
      }
      toast.error(shareErrorMessage(error, t))
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    if (!canSubmit) return
    if (preview.length > 0) setDelegations(preview)
    else void submit(false)
  }

  return (
    <form className="space-y-4" onSubmit={onSubmit} aria-label={t('form.title')}>
      {subject ? (
        <div className="flex items-center justify-between gap-2 rounded-md border border-border px-3 py-2">
          <span className="min-w-0 truncate text-sm">
            {subject.displayName}
            <span className="ml-2 text-xs text-muted-foreground">
              {t(`principalType.${subject.type}`)}
            </span>
          </span>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label={t('form.clearRecipient', { name: subject.displayName })}
            onClick={() => {
              setSubject(null)
              setDelegations(null)
            }}
          >
            <XIcon className="size-4" />
          </Button>
        </div>
      ) : (
        <PrincipalPicker
          types={backend.principalTypes}
          excludeKeys={excludeKeys}
          label={t('form.recipient')}
          disabled={!backend.snapshot?.canShare}
          onPick={setSubject}
        />
      )}

      <fieldset className="space-y-2" aria-describedby={`${levelGroupId}-hint`}>
        <legend className="text-xs font-medium text-muted-foreground">{t('form.level')}</legend>
        <div className="grid gap-2 sm:grid-cols-2">
          {backend.levels.map((value) => (
            <label
              key={value}
              htmlFor={`${levelGroupId}-${value}`}
              className="flex min-h-10 cursor-pointer items-start gap-2 rounded-md border border-border px-3 py-2 has-checked:border-primary-strong"
            >
              <input
                id={`${levelGroupId}-${value}`}
                type="radio"
                aria-label={t(levelLabelKey(backend.surface, value))}
                name={levelGroupId}
                value={value}
                checked={level === value}
                onChange={() => setLevel(value)}
                className="mt-1"
              />
              <span>
                <span className="block text-sm text-foreground">
                  {t(levelLabelKey(backend.surface, value))}
                </span>
                <span className="block text-xs text-muted-foreground">
                  {t(`levelHelp.${value}`)}
                </span>
              </span>
            </label>
          ))}
        </div>
        <p id={`${levelGroupId}-hint`} className="text-xs text-muted-foreground">
          {t('form.levelHint')}
        </p>
      </fieldset>

      {expiryAllowed ? (
        <div className="space-y-1.5">
          <label htmlFor={expiryId}>{t('form.expiry')}</label>
          <Input
            id={expiryId}
            type="date"
            value={expiry}
            min={localToday()}
            onChange={(event) => setExpiry(event.target.value)}
          />
          <p className="text-xs text-muted-foreground">{t('form.expiryHint')}</p>
        </div>
      ) : null}

      {requiresAccountAck ? (
        <div className="space-y-2 rounded-md border border-status-warn/40 bg-status-warn/10 px-3 py-2 text-xs">
          <p role="note">{t('notice.sharedAccount')}</p>
          <label className="flex items-center gap-2 text-foreground">
            <Checkbox
              aria-labelledby={ackId}
              checked={acknowledged}
              onCheckedChange={(checked) => setAcknowledged(checked === true)}
            />
            <span id={ackId}>{t('notice.sharedAccountAck')}</span>
          </label>
        </div>
      ) : null}

      {personal.length > 0 ? (
        <div role="alert" className="rounded-md border border-destructive/40 px-3 py-2 text-xs">
          <p className="font-medium">{t('personal.title')}</p>
          <ul className="list-disc pl-5">
            {personal.map((use) => (
              <li key={use.key}>
                {t(
                  PERSONAL_TYPES.has(use.resourceType)
                    ? `delegation.type.${use.resourceType}`
                    : 'delegation.type.other',
                )}{' '}
                · <code>{use.definitionKey}</code>
              </li>
            ))}
          </ul>
          <p className="mt-1 text-muted-foreground">{t('personal.hint')}</p>
        </div>
      ) : null}

      {preview.length > 0 && !delegations ? (
        <section
          aria-labelledby={previewId}
          className="space-y-1 rounded-md border border-border px-3 py-2 text-xs"
        >
          <p id={previewId} className="font-medium text-foreground">
            {t('delegation.previewTitle')}
          </p>
          <p className="text-muted-foreground">{t('delegation.previewDescription')}</p>
          <DelegationList delegations={preview} />
        </section>
      ) : null}

      {delegations ? (
        <DelegationConfirm
          delegations={delegations}
          pending={backend.pending}
          onConfirm={() => void submit(true)}
          onCancel={() => setDelegations(null)}
        />
      ) : null}

      <div className="flex justify-end">
        <Button type="submit" disabled={!canSubmit}>
          {backend.pending ? t('form.sharing') : t('form.submit')}
        </Button>
      </div>
    </form>
  )
}
