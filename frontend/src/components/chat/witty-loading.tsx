'use client'

import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'

const WITTY_MESSAGE_COUNT = 27

interface WittyLoadingMessageProps {
  className?: string
}

/**
 * 위트 있는 로딩 메시지 컴포넌트.
 * 3초 간격으로 랜덤 메시지 로테이션, fade 전환.
 * 이전 5개 메시지 중복 방지.
 * ThinkingDots 3-dot 애니메이션 함께 표시.
 */
export function WittyLoadingMessage({ className }: WittyLoadingMessageProps) {
  const t = useTranslations('chat.loading.witty')
  const messages = useMemo(
    () => Array.from({ length: WITTY_MESSAGE_COUNT }, (_, i) => t(String(i))),
    [t],
  )

  const [message, setMessage] = useState(() => pickRandom(messages, []))
  const [fading, setFading] = useState(false)
  const recentRef = useRef<string[]>([])

  const rotate = useCallback(() => {
    setFading(true)
    setTimeout(() => {
      const next = pickRandom(messages, recentRef.current)
      recentRef.current = [...recentRef.current.slice(-4), next]
      setMessage(next)
      setFading(false)
    }, 300)
  }, [messages])

  useEffect(() => {
    const interval = setInterval(rotate, 3000)
    return () => clearInterval(interval)
  }, [rotate])

  return (
    <div className={cn('flex items-center gap-3', className)}>
      <ThinkingDots />
      <span
        className={cn(
          'text-xs text-muted-foreground transition-opacity duration-300',
          fading ? 'opacity-0' : 'opacity-100',
        )}
      >
        {message}
      </span>
    </div>
  )
}

/** 3-dot 펄싱 애니메이션 (기존 ThinkingDots 스타일 유지) */
function ThinkingDots() {
  return (
    <div className="flex items-center gap-1.5">
      <span className="size-2 animate-pulse rounded-full bg-primary/50 [animation-delay:0ms] [animation-duration:1.4s]" />
      <span className="size-2 animate-pulse rounded-full bg-primary/50 [animation-delay:200ms] [animation-duration:1.4s]" />
      <span className="size-2 animate-pulse rounded-full bg-primary/50 [animation-delay:400ms] [animation-duration:1.4s]" />
    </div>
  )
}

/** 최근 N개를 제외한 랜덤 메시지 선택 */
function pickRandom(pool: string[], recent: string[]): string {
  const available = pool.filter((m) => !recent.includes(m))
  const source = available.length > 0 ? available : pool
  return source[Math.floor(Math.random() * source.length)]
}
