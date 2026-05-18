'use client'

import { useState, useEffect, useRef, useMemo } from 'react'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'

const WITTY_MESSAGE_COUNT = 27
const ROTATE_INTERVAL_MS = 3000
const FADE_DURATION_MS = 300

interface WittyLoadingMessageProps {
  className?: string
}

// 모듈 레벨 상태 — 컴포넌트가 streaming 중 remount 되더라도 (assistant-ui 가
// 청크마다 메시지 트리를 재구성하면 발생) 메시지 텍스트와 다음 rotate 시각이
// 유지된다. 이전엔 ``useState(() => pickRandom(...))`` 초기값이 mount 마다
// 새로 추첨되어 스트리밍 청크 타이밍에 메시지가 휘둘렸다.
let _currentMessage: string | null = null
let _recent: string[] = []
let _nextRotateAt = 0

/**
 * 위트 있는 로딩 메시지 컴포넌트.
 * 3초 간격으로 랜덤 메시지 로테이션, fade 전환.
 * 이전 5개 메시지 중복 방지. ThinkingDots 3-dot 애니메이션 함께 표시.
 */
export function WittyLoadingMessage({ className }: WittyLoadingMessageProps) {
  const t = useTranslations('chat.loading.witty')
  const messages = useMemo(
    () => Array.from({ length: WITTY_MESSAGE_COUNT }, (_, i) => t(String(i))),
    [t],
  )

  const [message, setMessage] = useState(() => {
    if (_currentMessage !== null && messages.includes(_currentMessage)) {
      return _currentMessage
    }
    const initial = pickRandom(messages, _recent)
    _currentMessage = initial
    _recent = [..._recent.slice(-4), initial]
    _nextRotateAt = Date.now() + ROTATE_INTERVAL_MS
    return initial
  })
  const [fading, setFading] = useState(false)

  const messagesRef = useRef(messages)
  useEffect(() => {
    messagesRef.current = messages
  }, [messages])

  // setInterval 이 아니라 setTimeout 체이닝으로 다음 rotate 시각을 모듈 상태에
  // 묶어둔다. 컴포넌트가 remount 되어도 ``_nextRotateAt`` 까지 남은 시간만큼만
  // 대기하므로, 청크가 빈번해도 회전 주기는 일정하게 ~3 초로 유지된다.
  useEffect(() => {
    let cancelled = false

    const scheduleNext = () => {
      const wait = Math.max(0, _nextRotateAt - Date.now())
      const timer = setTimeout(() => {
        if (cancelled) return
        setFading(true)
        setTimeout(() => {
          if (cancelled) return
          const next = pickRandom(messagesRef.current, _recent)
          _recent = [..._recent.slice(-4), next]
          _currentMessage = next
          _nextRotateAt = Date.now() + ROTATE_INTERVAL_MS
          setMessage(next)
          setFading(false)
          scheduleNext()
        }, FADE_DURATION_MS)
      }, wait)
      return timer
    }

    const timer = scheduleNext()
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [])

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
