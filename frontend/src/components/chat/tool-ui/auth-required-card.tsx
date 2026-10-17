'use client'

import { useState } from 'react'
import { useParams } from 'next/navigation'
import type { ToolCallMessagePartProps } from '@assistant-ui/react'
import { KeyRoundIcon, Loader2Icon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { AuthCardConnect } from '@/components/readiness/auth-card-connect'
import { AuthCardOAuthConnect } from './auth-card-oauth-connect'
import { cn } from '@/lib/utils'
import { useHiTL } from '@/lib/chat/hitl-context'
import {
  AUTH_REQUIRED_RESPONSES,
  authRequiredDecision,
  authRequiredItems,
  type AuthRequiredResponse,
} from '@/lib/chat/auth-required-interrupt'

interface AuthRequiredArgs {
  reason?: string
  items?: unknown
  attempt?: number
  hitl_action_index?: number
  hitl_interrupt_id?: string | null
}

const REASON_KEYS: Record<string, string> = {
  auth_needed: 'reasonAuthNeeded',
  expired: 'reasonExpired',
  scope_upgrade: 'reasonScopeUpgrade',
}

/**
 * `auth_required` card (plan 11.5, S22): lists the connections a paused run needs and
 * answers with `retry` / `skip` / `cancel`. My OAuth connections get "연결하기" (popup +
 * shared OAuth receiver, G-05), answering `retry` once authorized; when the agent's S21
 * check can bind one of my other connections, "연결하기" opens it and answers `retry`
 * after the S23 dialog connected; otherwise a note points to the credentials screen
 * (G-04); owner-fixable items
 * only say the owner was notified. A re-ask (`attempt` > 1) says it is still not connected.
 */
export function AuthRequiredCard({ args }: ToolCallMessagePartProps<AuthRequiredArgs, unknown>) {
  const t = useTranslations('chat.authRequired')
  const hitl = useHiTL()
  const [state, setState] = useState<'idle' | 'sending' | 'sent'>('idle')
  const [failed, setFailed] = useState(false)
  const routeAgentId = useParams<{ agentId?: string }>()?.agentId
  // The thread's own agent (side chat may sit on another agent's route), else the route's.
  const agentId = hitl?.agentId || routeAgentId
  const askedAttempt = typeof args?.attempt === 'number' ? args.attempt : 1
  const items = authRequiredItems(args)
  const oauthItems = items.filter(
    (item) => item.fixableBy === 'me' && item.connectKind === 'oauth' && item.credentialId,
  )
  const mine = items
    .filter((item) => item.fixableBy === 'me' && !oauthItems.includes(item))
    .map((item) => item.resourceName)
  const reasonKey =
    REASON_KEYS[typeof args?.reason === 'string' ? args.reason : ''] ?? 'reasonAuthNeeded'

  async function answer(response: AuthRequiredResponse) {
    setState('sending')
    setFailed(false)
    try {
      // G-06: the attempt lets the server refuse an answer to an older re-ask.
      const attempt = typeof args?.attempt === 'number' ? args.attempt : undefined
      const decision = authRequiredDecision(response, attempt)
      if (typeof args?.hitl_action_index === 'number' && hitl?.registerDecision) {
        await hitl.registerDecision(
          args.hitl_action_index,
          decision,
          t(response),
          args.hitl_interrupt_id,
        )
      } else {
        await hitl?.onResumeDecisions([decision], t(response))
      }
      setState('sent')
    } catch {
      setState('idle')
      setFailed(true)
    }
  }

  return (
    <div className="moldy-chat-card moldy-status-warn w-full overflow-hidden border border-border bg-card text-foreground">
      <div className="flex items-center gap-2 border-b border-border/60 px-4 py-3">
        <KeyRoundIcon className="moldy-status-icon size-4" />
        <span className="text-sm font-medium">{t('title')}</span>
      </div>
      <div className="space-y-3 p-4 text-sm">
        <p className="text-muted-foreground">{t(reasonKey)}</p>
        {askedAttempt > 1 && state !== 'sent' && (
          <p className="text-xs text-muted-foreground">{t('stillNotConnected')}</p>
        )}
        <ul className="space-y-1.5">
          {items.map((item) => (
            <li key={`${item.resourceName}:${item.toolNames.join(',')}`}>
              <span className="font-medium">{item.resourceName}</span>
              {item.toolNames.length > 0 && (
                <span className="text-muted-foreground"> · {item.toolNames.join(', ')}</span>
              )}
              {item.fixableBy === 'owner' && (
                <span className="block text-xs text-muted-foreground">{t('ownerNotified')}</span>
              )}
            </li>
          ))}
        </ul>
        {failed && (
          <p role="alert" className="text-xs text-destructive">
            {t('resumeFailed')}
          </p>
        )}
        {state === 'sent' ? (
          <p className="text-xs text-muted-foreground">{t('sent')}</p>
        ) : (
          <div className="flex flex-wrap justify-end gap-2">
            {oauthItems.map((item) => (
              <AuthCardOAuthConnect
                key={item.credentialId}
                credentialId={item.credentialId ?? ''}
                resourceName={item.resourceName}
                disabled={state === 'sending'}
                onConnected={() => void answer('retry')}
              />
            ))}
            {agentId && mine.length > 0 && (
              <AuthCardConnect
                agentId={agentId}
                resourceNames={mine}
                disabled={state === 'sending'}
                onConnected={() => void answer('retry')}
              />
            )}
            {AUTH_REQUIRED_RESPONSES.map((response) => (
              <button
                key={response}
                type="button"
                disabled={state === 'sending'}
                onClick={() => void answer(response)}
                className={cn(
                  'flex items-center gap-1.5 rounded-full px-4 py-2 text-xs font-medium transition-[background-color,color,opacity] disabled:cursor-not-allowed disabled:opacity-60',
                  response === 'retry'
                    ? 'bg-primary text-primary-foreground hover:bg-primary/90'
                    : 'bg-muted text-foreground hover:bg-muted/80',
                )}
              >
                {state === 'sending' && response === 'retry' && (
                  <Loader2Icon className="size-3 animate-spin" />
                )}
                {t(response)}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
