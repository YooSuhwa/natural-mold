'use client'

/**
 * Shared OAuth completion receiver (org-authz plan 13.6, G-05).
 *
 * The OAuth callback page (`GET /api/oauth2-credential/callback`) posts
 * `{type: 'moldy.oauth.completed', credentialId}` to its opener. Whoever started the
 * flow — the MCP wizard, the chat auth card, the per-user connection dialog — listens
 * with `useOAuthCompletion`: the origin is checked, the credential is matched and the
 * credential/readiness queries are refreshed before `onCompleted` runs.
 */
import { useCallback, useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { API_BASE } from '@/lib/api/client'
import { navigateOpenedWindow, openNamedPopupWindow } from '@/lib/browser/window-open'
import { useStartOAuth2 } from '@/lib/hooks/use-credential-test'
import { credentialQueryKeys } from '@/lib/query-keys/credentials'
import { readinessQueryKeys } from '@/lib/query-keys/readiness'

export type OAuthCompletedMessage = {
  readonly type: 'moldy.oauth.completed'
  readonly credentialId?: string
}

export function isOAuthCompletedMessage(data: unknown): data is OAuthCompletedMessage {
  if (!data || typeof data !== 'object') return false
  if (!('type' in data) || data.type !== 'moldy.oauth.completed') return false
  if (!('credentialId' in data)) return true
  return typeof data.credentialId === 'string' || data.credentialId === undefined
}

function allowedOrigins(): Set<string> {
  const origins = new Set([window.location.origin])
  if (URL.canParse(API_BASE)) origins.add(new URL(API_BASE).origin)
  return origins
}

export interface OAuthCompletionOptions {
  /** The credential the flow was started for; a message naming another one is ignored. */
  readonly credentialId: string | null
  readonly enabled?: boolean
  /** Receives the completed credential id (the message's, else `credentialId`). */
  readonly onCompleted: (credentialId: string | null) => void
}

export function useOAuthCompletion({
  credentialId,
  enabled = true,
  onCompleted,
}: OAuthCompletionOptions): void {
  const queryClient = useQueryClient()
  const onCompletedRef = useRef(onCompleted)
  useEffect(() => {
    onCompletedRef.current = onCompleted
  })

  useEffect(() => {
    if (!enabled) return
    function handleMessage(event: MessageEvent) {
      if (!isOAuthCompletedMessage(event.data)) return
      if (!allowedOrigins().has(event.origin)) return
      const completedId = event.data.credentialId ?? null
      if (completedId && credentialId && completedId !== credentialId) return
      void queryClient.invalidateQueries({ queryKey: credentialQueryKeys.all })
      if (completedId) {
        void queryClient.invalidateQueries({ queryKey: credentialQueryKeys.detail(completedId) })
      }
      void queryClient.invalidateQueries({ queryKey: readinessQueryKeys.all })
      onCompletedRef.current(completedId ?? credentialId)
    }
    window.addEventListener('message', handleMessage)
    return () => window.removeEventListener('message', handleMessage)
  }, [credentialId, enabled, queryClient])
}

export type OAuthAuthorizeResult = 'started' | 'blocked' | 'failed'

/** Open the named popup synchronously (inside the click), before any await. */
export function openOAuthPopup(): Window | null {
  return openNamedPopupWindow('moldy-oauth', 'popup,width=560,height=760')
}

/** Start `POST /api/oauth2-credential/auth/{id}` and send the popup to the provider. */
export function useOAuthAuthorize() {
  const { mutateAsync, isPending } = useStartOAuth2()
  const authorize = useCallback(
    async (
      credentialId: string,
      popup: Window | null = openOAuthPopup(),
    ): Promise<OAuthAuthorizeResult> => {
      if (!popup) return 'blocked'
      try {
        const { authorization_url: url } = await mutateAsync(credentialId)
        if (!navigateOpenedWindow(popup, url)) {
          popup.close()
          return 'failed'
        }
        popup.focus()
        return 'started'
      } catch {
        popup.close()
        return 'failed'
      }
    },
    [mutateAsync],
  )
  return { authorize, starting: isPending }
}
