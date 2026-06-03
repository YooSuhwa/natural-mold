'use client'

import { useRef, type ReactNode } from 'react'
import { useTranslations } from 'next-intl'
import {
  BuilderButton,
  BuilderFeedbackWrap,
  BuilderTextarea,
} from './builder-primitives'

interface BuilderFeedbackTextareaProps {
  value: string
  onChange: (v: string) => void
  disabled?: boolean
  placeholder?: string
  rows?: number
}

/**
 * Builder approval/edit 카드 공통 textarea.
 *
 * - mint focus ring (`primaryDim` border + 3px box-shadow)
 * - 한글 IME composition 가드 (composition 중 Enter 전파 차단)
 * - disabled 시 회색 처리 없이 비활성만 — 시각적으로는 frozen 상태에서 보통 unmount
 */
export function BuilderFeedbackTextarea({
  value,
  onChange,
  disabled = false,
  placeholder,
  rows = 2,
}: BuilderFeedbackTextareaProps) {
  const t = useTranslations('chat.builderApproval')
  const resolvedPlaceholder = placeholder ?? t('shortPlaceholder')
  const composingRef = useRef(false)
  return (
    <BuilderFeedbackWrap>
      <BuilderTextarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onCompositionStart={() => {
          composingRef.current = true
        }}
        onCompositionEnd={() => {
          composingRef.current = false
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && composingRef.current) e.stopPropagation()
        }}
        placeholder={resolvedPlaceholder}
        rows={rows}
        disabled={disabled}
      />
    </BuilderFeedbackWrap>
  )
}

interface ActionButtonProps {
  onClick: () => void
  disabled?: boolean
  label: string
  icon?: ReactNode
}

/** 민트 primary 버튼 — 승인/생성/확정 등 메인 액션. */
export function MintActionButton({ onClick, disabled, label, icon }: ActionButtonProps) {
  return (
    <BuilderButton tone="primary" onClick={onClick} disabled={disabled} className="px-4">
      {icon}
      {label}
    </BuilderButton>
  )
}

/** 흰 outline 버튼 — 수정 요청/넘어가기/재생성 등 보조 액션. */
export function OutlineActionButton({ onClick, disabled, label, icon }: ActionButtonProps) {
  return (
    <BuilderButton tone="secondary" onClick={onClick} disabled={disabled}>
      {icon}
      {label}
    </BuilderButton>
  )
}
