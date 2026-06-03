'use client'

import { ArrowRightIcon, CheckIcon } from 'lucide-react'

export type SystemEventKind = 'completed' | 'started'

export interface SystemEventChipProps {
  /** 메인 라벨. 예: `Phase 2 완료`, `Phase 3 시작` */
  label: string
  /** 보조 라벨. 예: `사용자 의도 분석` */
  sublabel?: string
  /** 디스크 아이콘 변종. 기본 `completed` (체크) / `started` (오른쪽 화살표). */
  kind?: SystemEventKind
}

/**
 * 메시지 사이에 들어가는 phase 전환 칩.
 *
 * Builder의 phase 시작/완료 알림을 메시지로 baking하지 않고 별도 칩으로 표시.
 * 아바타·이름 없는 centered pill (designer-directed).
 */
export function SystemEventChip({ label, sublabel, kind = 'completed' }: SystemEventChipProps) {
  const Icon = kind === 'completed' ? CheckIcon : ArrowRightIcon
  return (
    <div role="status" className="moldy-system-event flex justify-center">
      <div className="moldy-system-event-chip">
        <span className="moldy-system-event-icon">
          <Icon className="size-2.5" strokeWidth={3.5} />
        </span>
        <span>{label}</span>
        {sublabel && <span className="moldy-system-event-subtle">· {sublabel}</span>}
      </div>
    </div>
  )
}
