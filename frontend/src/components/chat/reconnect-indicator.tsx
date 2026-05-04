'use client'

import { useAtomValue } from 'jotai'
import { Loader2 } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { reconnectStateAtom } from '@/lib/stores/chat-store'

/** SSE stream 이 끊겨 자동 재연결 중일 때만 보이는 배지. 실패 시 toast 로
 *  알리고 배지는 idle 로 즉시 복귀. */
export function ReconnectIndicator() {
  const state = useAtomValue(reconnectStateAtom)
  const t = useTranslations('chat.reconnect')
  if (state !== 'reconnecting') return null
  return (
    <div className="flex justify-center pb-2">
      <div
        role="status"
        aria-live="polite"
        className="inline-flex items-center gap-2 rounded-full border border-border bg-muted/70 px-3 py-1 text-xs text-muted-foreground shadow-sm"
      >
        <Loader2 className="size-3 animate-spin" />
        <span>{t('reconnecting')}</span>
      </div>
    </div>
  )
}
