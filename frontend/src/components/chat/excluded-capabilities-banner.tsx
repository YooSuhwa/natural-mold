'use client'

import { useMemo } from 'react'
import { ShieldAlertIcon, XIcon } from 'lucide-react'
import { useAtomValue, useSetAtom } from 'jotai'
import { selectAtom } from 'jotai/utils'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import { useChatConversationId } from '@/components/chat/conversation-context'
import {
  chatExcludedCapabilitiesAtom,
  dismissExcludedCapabilitiesAtom,
  excludedCapabilitiesScope,
  type ExcludedCapabilityGroup,
} from '@/lib/stores/chat-excluded-capabilities'

const KNOWN_TYPES = new Set(['tool', 'mcp_tool', 'skill', 'agent', 'llm_model'])

function typeKey(type: string): string {
  return KNOWN_TYPES.has(type) ? type : 'other'
}

function GroupLabel({ group }: { readonly group: ExcludedCapabilityGroup }) {
  const t = useTranslations('chat.excludedCapabilities')
  return (
    <li
      className="rounded-full bg-status-warn/15 px-2 py-0.5 moldy-ui-micro font-medium text-foreground"
      data-moldy-excluded-group={`${group.type}:${group.reason}`}
    >
      {t('group', {
        type: t(`type.${typeKey(group.type)}`),
        count: group.count,
        reason: t(`reason.${group.reason}`),
      })}
    </li>
  )
}

/**
 * ExcludedCapabilitiesBanner — one dismissible line at the top of the transcript when the
 * run's access plan left out optional capabilities (F-06 / plan 13.6). Data comes from the
 * `moldy.excluded_capabilities` run-start event (counts per type and reason, no ids).
 */
export function ExcludedCapabilitiesBanner() {
  const t = useTranslations('chat.excludedCapabilities')
  const conversationId = useChatConversationId()
  const scope = excludedCapabilitiesScope(conversationId)
  // Subscribe to this conversation's notice only (not every conversation's run churn).
  const noticeAtom = useMemo(
    () => selectAtom(chatExcludedCapabilitiesAtom, (state) => state[scope]?.notice ?? null),
    [scope],
  )
  const notice = useAtomValue(noticeAtom)
  const dismiss = useSetAtom(dismissExcludedCapabilitiesAtom)

  // Defensive against partial state (blanket `useAtomValue` test mocks, stale shapes):
  // anything without a groups array renders nothing instead of crashing the thread.
  const groups = notice && !notice.dismissed && Array.isArray(notice.groups) ? notice.groups : []
  if (groups.length === 0) return null
  const hasDenied = groups.some((group) => group.reason === 'denied')

  return (
    <div
      role="status"
      className="border-b border-status-warn/40 bg-status-warn/10 px-4 py-1.5"
      data-moldy-excluded-capabilities="true"
    >
      <div className="mx-auto flex w-full max-w-3xl items-start gap-2">
        <ShieldAlertIcon aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-status-warn" />
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <p className="moldy-ui-caption font-medium text-foreground">{t('title')}</p>
            <ul className="flex flex-wrap gap-1" aria-label={t('listLabel')}>
              {groups.map((group) => (
                <GroupLabel key={`${group.type}:${group.reason}`} group={group} />
              ))}
            </ul>
          </div>
          {hasDenied ? (
            <p className="moldy-ui-caption text-muted-foreground">{t('requestAccessHint')}</p>
          ) : null}
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          aria-label={t('dismiss')}
          onClick={() => dismiss(conversationId)}
        >
          <XIcon aria-hidden="true" />
        </Button>
      </div>
    </div>
  )
}
