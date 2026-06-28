'use client'

import { useCallback, useSyncExternalStore } from 'react'
import { MessageSquareIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'

const HIGHLIGHT_MS = 1500

/**
 * `[data-moldy-message-id="<id>"]` 앵커를 조회한다. 메시지 id는 UUID 계열이지만
 * selector 안전을 위해 ``CSS.escape``(있으면)로 이스케이프한다.
 */
function findMessageAnchor(messageId: string): HTMLElement | null {
  if (typeof document === 'undefined') return null
  const escaped =
    typeof CSS !== 'undefined' && typeof CSS.escape === 'function'
      ? CSS.escape(messageId)
      : messageId.replace(/["\\]/g, '\\$&')
  const el = document.querySelector(`[data-moldy-message-id="${escaped}"]`)
  return el instanceof HTMLElement ? el : null
}

/** 메시지가 현재 로드된 transcript(가상화 없음)에 존재하는지. */
export function messageAnchorExists(messageId: string): boolean {
  return findMessageAnchor(messageId) !== null
}

/**
 * 로드된 transcript에서 해당 메시지로 스크롤 + 잠깐 하이라이트한다.
 * 앵커가 없으면(다른 페이지의 메시지) 아무 동작 없이 false를 반환한다.
 */
export function jumpToMessage(messageId: string): boolean {
  const el = findMessageAnchor(messageId)
  if (!el) return false
  el.scrollIntoView({ block: 'center', behavior: 'smooth' })
  el.classList.add('moldy-jump-highlight')
  window.setTimeout(() => el.classList.remove('moldy-jump-highlight'), HIGHLIGHT_MS)
  return true
}

const NOOP = () => () => {}

/**
 * 메시지 앵커가 현재 transcript에 존재하는지 DOM에서 구독한다.
 * transcript는 가상화되지 않으므로 "로드된 메시지"에 대해서만 앵커가 있다.
 * ``useSyncExternalStore``로 외부 mutable 소스(DOM)를 읽어 effect-setState 없이
 * 렌더 중 스냅샷을 얻고, body mutation을 관찰해 메시지가 늦게 로드돼도 갱신한다.
 */
function useMessageInLoadedPage(messageId: string | null | undefined): boolean {
  const subscribe = useCallback((onStoreChange: () => void) => {
    if (typeof MutationObserver === 'undefined' || typeof document === 'undefined') return NOOP()
    const observer = new MutationObserver(onStoreChange)
    observer.observe(document.body, { childList: true, subtree: true })
    return () => observer.disconnect()
  }, [])
  const getSnapshot = useCallback(
    () => (messageId ? messageAnchorExists(messageId) : false),
    [messageId],
  )
  return useSyncExternalStore(subscribe, getSnapshot, () => false)
}

/**
 * 파일 → 대화 메시지로 이동하는 액션.
 * - 메시지가 로드된 페이지에 있으면 "대화로 이동" 버튼.
 * - 없으면(이전 페이지) 비활성 "이전 메시지" 라벨 + 네이티브 tooltip.
 * - message_id가 없으면 렌더하지 않는다.
 */
export function JumpToMessageButton({
  messageId,
  className,
}: {
  messageId: string | null | undefined
  className?: string
}) {
  const t = useTranslations('chat.files')
  const inLoadedPage = useMessageInLoadedPage(messageId)

  if (!messageId) return null

  if (!inLoadedPage) {
    return (
      <span
        className={cn(
          'inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground/70',
          className,
        )}
        title={t('notInLoaded')}
        aria-disabled="true"
      >
        <MessageSquareIcon className="size-3.5" aria-hidden />
        {t('notInLoaded')}
      </span>
    )
  }

  return (
    <button
      type="button"
      onClick={() => jumpToMessage(messageId)}
      className={cn(
        'inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground',
        'focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring',
        className,
      )}
    >
      <MessageSquareIcon className="size-3.5" aria-hidden />
      {t('jumpToMessage')}
    </button>
  )
}
