'use client'

import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * HITL Countdown Timer + Auto-Extend
 *
 * - 만료 deadline까지 1초 tick으로 잔여시간을 갱신
 * - 60초 이하 → urgent 플래그 true
 * - 폼 인터랙션 시 extend() 호출 → 30초 cooldown 내 1회만 +60s 연장
 * - 만료 시 onExpire 1회 호출 (자동 reject 등)
 */

const DEFAULT_TIMEOUT_S = 300 // 5분
const EXTEND_AMOUNT_S = 60
const EXTEND_COOLDOWN_MS = 30_000
const URGENT_THRESHOLD_S = 60

export interface UseApprovalDeadlineOptions {
  approvalId: string
  initialTimeoutSeconds?: number
  onExpire: () => void
  /** false면 timer 중단 (예: 이미 결정 완료된 카드) */
  active?: boolean
}

export interface UseApprovalDeadlineReturn {
  /** 잔여 초 (0 ~ initialTimeout) */
  remaining: number
  /** 60초 이하 + 활성 상태 */
  isUrgent: boolean
  /** "MM:SS" 포맷 */
  formatted: string
  /** 폼 인터랙션 시 호출 — cooldown 내면 무시 */
  extend: () => void
}

export function useApprovalDeadline({
  approvalId,
  initialTimeoutSeconds,
  onExpire,
  active = true,
}: UseApprovalDeadlineOptions): UseApprovalDeadlineReturn {
  const initial = initialTimeoutSeconds ?? DEFAULT_TIMEOUT_S
  // mount 후 effect에서 초기화 — useRef 초기값에 Date.now() 사용 금지(react-hooks/purity)
  const deadlineRef = useRef<number | null>(null)
  const lastExtendRef = useRef<number>(0)
  const expiredFiredRef = useRef(false)
  const onExpireRef = useRef(onExpire)
  const [remaining, setRemaining] = useState<number>(initial)

  // onExpire가 변해도 effect를 재구동하지 않도록 ref 동기화
  useEffect(() => {
    onExpireRef.current = onExpire
  }, [onExpire])

  // approvalId(또는 initial timeout) 변경 시 deadline 리셋
  // setState는 tick effect에서 처리 (set-state-in-effect 룰 회피)
  useEffect(() => {
    deadlineRef.current = Date.now() + initial * 1000
    lastExtendRef.current = 0
    expiredFiredRef.current = false
  }, [approvalId, initial])

  useEffect(() => {
    if (!active) return undefined

    const tick = () => {
      const deadline = deadlineRef.current
      if (deadline === null) return
      const next = Math.max(0, (deadline - Date.now()) / 1000)
      setRemaining((prev) => (Math.ceil(prev) === Math.ceil(next) ? prev : next))
      if (next <= 0 && !expiredFiredRef.current) {
        expiredFiredRef.current = true
        onExpireRef.current()
      }
    }
    // 초기 동기화 — interval 첫 호출까지 1초 대기 방지
    const start = setTimeout(tick, 0)
    const id = setInterval(tick, 1000)
    return () => {
      clearTimeout(start)
      clearInterval(id)
    }
  }, [active, approvalId, initial])

  const extend = useCallback(() => {
    if (!active) return
    const now = Date.now()
    if (now - lastExtendRef.current < EXTEND_COOLDOWN_MS) return
    lastExtendRef.current = now
    // 이미 만료됐다면 now를 기준으로 다시 시작
    const current = deadlineRef.current ?? now
    const base = Math.max(current, now)
    deadlineRef.current = base + EXTEND_AMOUNT_S * 1000
    expiredFiredRef.current = false
  }, [active])

  return {
    remaining,
    isUrgent: active && remaining > 0 && remaining <= URGENT_THRESHOLD_S,
    formatted: formatTime(remaining),
    extend,
  }
}

function formatTime(seconds: number): string {
  const total = Math.ceil(Math.max(0, seconds))
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${s.toString().padStart(2, '0')}`
}
