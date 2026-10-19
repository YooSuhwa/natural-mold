'use client'

import { useState } from 'react'
import { Loader2Icon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { useOAuthAuthorize, useOAuthCompletion } from '@/lib/hooks/use-oauth-completion'

interface AuthCardOAuthConnectProps {
  readonly credentialId: string
  readonly resourceName: string
  readonly disabled: boolean
  readonly onConnected: () => void
}

/**
 * The S22 card's "연결하기" for one of my OAuth connections (plan 11.5, 13.6): opens the
 * `POST /api/oauth2-credential/auth/{id}` popup and, once the shared receiver hears
 * `moldy.oauth.completed` for that credential, lets the card answer `retry`.
 */
export function AuthCardOAuthConnect({
  credentialId,
  resourceName,
  disabled,
  onConnected,
}: AuthCardOAuthConnectProps) {
  const t = useTranslations('chat.authRequired')
  const { authorize, starting } = useOAuthAuthorize()
  const [waiting, setWaiting] = useState(false)

  useOAuthCompletion({
    credentialId,
    enabled: waiting,
    onCompleted: () => {
      setWaiting(false)
      onConnected()
    },
  })

  async function start() {
    const result = await authorize(credentialId)
    if (result === 'started') {
      setWaiting(true)
      return
    }
    toast.error(t(result === 'blocked' ? 'oauthPopupBlocked' : 'oauthStartFailed'))
  }

  return (
    <button
      type="button"
      disabled={disabled || starting}
      onClick={() => void start()}
      className="flex items-center gap-1.5 rounded-full bg-muted px-4 py-2 text-xs font-medium text-foreground transition-[background-color,opacity] hover:bg-muted/80 disabled:cursor-not-allowed disabled:opacity-60"
    >
      {(starting || waiting) && <Loader2Icon className="size-3 animate-spin" />}
      {waiting ? t('oauthWaiting') : t('connectOAuth', { name: resourceName })}
    </button>
  )
}
