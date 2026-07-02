'use client'

import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'
import { ChevronDownIcon, ChevronUpIcon, SearchIcon, XIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { jumpToMessage } from '@/components/chat/right-rail/jump-to-message'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  applySearchHighlights,
  clearSearchHighlights,
  collectMatchRanges,
} from '@/lib/chat/chat-search'

interface ChatSearchOverlayProps {
  onClose: () => void
  /** 검색 스코프. 여러 thread가 마운트된 페이지(설정 fix/test 탭)에서 이 thread의
   *  viewport로 한정한다. ref로 받아 렌더 중 .current 접근을 피한다(핸들러에서 읽음).
   *  없으면 document 전역. */
  searchRootRef?: RefObject<HTMLElement | null>
}

/**
 * 대화 내 검색(G6) Ctrl+F 오버레이. DOM 앵커에서 텍스트를 수집해 클라이언트 필터
 * 하고, 매치를 ``jumpToMessage``로 스크롤 + 하이라이트한다. Enter/Shift+Enter로
 * 다음/이전, Esc로 닫는다.
 */
export function ChatSearchOverlay({ onClose, searchRootRef }: ChatSearchOverlayProps) {
  const t = useTranslations('chat.search')
  const [query, setQuery] = useState('')
  const [matchIds, setMatchIds] = useState<readonly string[]>([])
  const [rangeMap, setRangeMap] = useState<ReadonlyMap<string, Range[]>>(() => new Map())
  const [current, setCurrent] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    inputRef.current?.focus()
    inputRef.current?.select()
    return () => clearSearchHighlights()
  }, [])

  function handleChange(value: string) {
    setQuery(value)
    const ranges = collectMatchRanges(value, searchRootRef?.current ?? document)
    const ids = Array.from(ranges.keys())
    setMatchIds(ids)
    setRangeMap(ranges)
    setCurrent(0)
    applySearchHighlights(ranges, ids[0])
    if (ids.length > 0) jumpToMessage(ids[0])
  }

  const go = useCallback(
    (delta: number) => {
      if (matchIds.length === 0) return
      const next = (current + delta + matchIds.length) % matchIds.length
      setCurrent(next)
      applySearchHighlights(rangeMap, matchIds[next])
      jumpToMessage(matchIds[next])
    },
    [matchIds, current, rangeMap],
  )

  function handleKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Escape') {
      event.preventDefault()
      onClose()
    } else if (event.key === 'Enter') {
      // IME 조합 확정 Enter(한국어/CJK)는 이동을 트리거하지 않는다.
      if (event.nativeEvent.isComposing) return
      event.preventDefault()
      go(event.shiftKey ? -1 : 1)
    }
  }

  const hasQuery = query.trim().length > 0
  const total = matchIds.length
  const position = total === 0 ? 0 : current + 1

  return (
    <div
      role="search"
      className="moldy-popover sticky top-2 z-20 mx-auto flex w-full max-w-md items-center gap-1.5 rounded-lg px-3 py-1.5"
    >
      <SearchIcon className="size-4 shrink-0 text-muted-foreground" />
      <Input
        ref={inputRef}
        value={query}
        onChange={(event) => handleChange(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={t('placeholder')}
        aria-label={t('placeholder')}
        className="h-8 flex-1 border-0 bg-transparent px-0 shadow-none focus-visible:ring-0"
      />
      {hasQuery ? (
        <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
          {t('count', { position, total })}
        </span>
      ) : null}
      <Button
        variant="ghost"
        size="icon-sm"
        onClick={() => go(-1)}
        disabled={total === 0}
        aria-label={t('previous')}
      >
        <ChevronUpIcon className="size-4" />
      </Button>
      <Button
        variant="ghost"
        size="icon-sm"
        onClick={() => go(1)}
        disabled={total === 0}
        aria-label={t('next')}
      >
        <ChevronDownIcon className="size-4" />
      </Button>
      <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label={t('close')}>
        <XIcon className="size-4" />
      </Button>
    </div>
  )
}
