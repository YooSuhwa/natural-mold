'use client'

import { BUILDER_TOKENS as T } from './tool-ui/builder-tokens'

export interface TypingDotsProps {
  /** 옆에 표시할 보조 라벨. 예: `의도를 정리하고 있어요…` */
  label?: string
}

/**
 * Builder 응답 진행 인디케이터.
 *
 * 3개 점이 cb-bounce(translateY + opacity)로 0.15초씩 stagger되어 움직임.
 * 옆에 label을 두면 typing 텍스트로 함께 표시.
 */
export function TypingDots({ label }: TypingDotsProps) {
  return (
    <div className="flex items-center gap-2 text-[12.5px]" style={{ color: T.muted }}>
      <span className="inline-flex items-center gap-1" style={{ paddingTop: 2 }}>
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="rounded-full"
            style={{
              width: 6,
              height: 6,
              background: T.primaryDim,
              animation: `cb-bounce 1.2s ease-in-out ${i * 0.15}s infinite`,
            }}
          />
        ))}
      </span>
      {label && <span>{label}</span>}
    </div>
  )
}
