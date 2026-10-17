'use client'

import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { copyText, invitationLink } from '../lib/invite-emails'

export interface InviteResult {
  email: string
  /** One-time secret: only ever copied on explicit action, never rendered. */
  token: string | null
  error: string | null
}

export function CopyInviteLinkButton({ token, email }: { token: string; email: string }) {
  const t = useTranslations('orgAdmin.members.invite')
  return (
    <Button
      size="sm"
      variant="outline"
      aria-label={t('copyFor', { email })}
      onClick={() => {
        void copyText(invitationLink(token, window.location.origin)).then((copied) =>
          copied ? toast.success(t('copied')) : toast.error(t('copyFailed')),
        )
      }}
    >
      {t('copy')}
    </Button>
  )
}

/** S11 result: per email, a copyable link, "already invited" or the error. */
export function InviteResultList({ results }: { results: readonly InviteResult[] }) {
  const t = useTranslations('orgAdmin.members.invite')
  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">{t('linkOnce')}</p>
      <ul className="divide-y divide-border">
        {results.map((result) => (
          <li key={result.email} className="flex items-center justify-between gap-3 py-2">
            <div className="min-w-0">
              <p className="truncate text-sm">{result.email}</p>
              <p
                className={
                  result.error ? 'text-xs text-status-danger' : 'text-xs text-muted-foreground'
                }
              >
                {result.error ?? (result.token ? t('created') : t('existing'))}
              </p>
            </div>
            {result.token ? (
              <CopyInviteLinkButton token={result.token} email={result.email} />
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  )
}
