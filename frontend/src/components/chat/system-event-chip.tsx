'use client'

import { ArrowRightIcon, CheckIcon } from 'lucide-react'
import { BUILDER_TOKENS as T } from './tool-ui/builder-tokens'

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
    <div role="status" className="flex justify-center" style={{ margin: '4px 0' }}>
      <div
        className="inline-flex items-center gap-2 text-[12px] font-semibold"
        style={{
          padding: '6px 13px 6px 9px',
          borderRadius: 999,
          background: T.primaryBg,
          border: `1px solid ${T.primaryBgStrong}`,
          color: T.primaryInk,
          letterSpacing: '-0.005em',
        }}
      >
        <span
          className="inline-flex items-center justify-center rounded-full text-white"
          style={{ width: 16, height: 16, background: T.primary }}
        >
          <Icon className="size-2.5" strokeWidth={3.5} />
        </span>
        <span>{label}</span>
        {sublabel && (
          <span className="font-medium" style={{ color: T.muted }}>
            · {sublabel}
          </span>
        )}
      </div>
    </div>
  )
}
