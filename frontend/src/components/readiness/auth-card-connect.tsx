'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useTranslations } from 'next-intl'

import { useAgentReadiness } from '@/lib/hooks/use-readiness'

import { readinessItemAction } from './readiness-actions'
import { ReadinessDialog } from './readiness-dialog'

interface AuthCardConnectProps {
  readonly agentId: string
  /** `fixable_by=me` connections the paused run asks for (card item names). */
  readonly resourceNames: readonly string[]
  readonly disabled: boolean
  readonly onConnected: () => void
}

/**
 * The S22 card's "연결하기": offered only when the agent's S21 check can connect one of
 * the asked connections in place (a per-user binding); otherwise the credential must be
 * reconnected on the credentials screen and the card keeps "다시 시도".
 */
export function AuthCardConnect({
  agentId,
  resourceNames,
  disabled,
  onConnected,
}: AuthCardConnectProps) {
  const t = useTranslations('readiness.authCard')
  const { data } = useAgentReadiness(agentId)
  const [open, setOpen] = useState(false)
  if (!data) return null
  const bindable = data.items.some(
    (item) =>
      readinessItemAction(item, agentId)?.type === 'bind' &&
      resourceNames.includes(item.name ?? ''),
  )
  if (!bindable) {
    return (
      <p className="w-full text-xs text-muted-foreground">
        {t('reconnectNote')}{' '}
        <Link href="/settings/credentials" className="text-primary-strong hover:underline">
          {t('openCredentials')}
        </Link>
      </p>
    )
  }
  return (
    <>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen(true)}
        className="rounded-full bg-muted px-4 py-2 text-xs font-medium text-foreground transition-[background-color,opacity] hover:bg-muted/80 disabled:cursor-not-allowed disabled:opacity-60"
      >
        {t('connect')}
      </button>
      {open ? (
        <ReadinessDialog
          open
          onOpenChange={setOpen}
          agentId={agentId}
          onConnected={() => {
            setOpen(false)
            onConnected()
          }}
        />
      ) : null}
    </>
  )
}
