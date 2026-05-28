'use client'

import { useRef, useState, type ReactNode } from 'react'
import { BUILDER_TOKENS as T } from './builder-tokens'

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
  placeholder = '수정 의견을 입력하세요',
  rows = 2,
}: BuilderFeedbackTextareaProps) {
  const [focused, setFocused] = useState(false)
  const composingRef = useRef(false)
  return (
    <div style={{ padding: '12px 14px 4px' }}>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        onCompositionStart={() => {
          composingRef.current = true
        }}
        onCompositionEnd={() => {
          composingRef.current = false
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && composingRef.current) e.stopPropagation()
        }}
        placeholder={placeholder}
        rows={rows}
        disabled={disabled}
        className="w-full resize-none font-sans text-[13.5px] outline-none transition-[border-color,box-shadow] duration-150"
        style={{
          padding: '10px 12px',
          background: T.surface,
          border: `1px solid ${focused ? T.primaryDim : T.border}`,
          borderRadius: 9,
          color: T.ink,
          lineHeight: 1.55,
          letterSpacing: '-0.005em',
          boxShadow: focused ? T.focusShadow : 'none',
        }}
      />
    </div>
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
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="inline-flex items-center gap-1.5 text-[12.5px] font-semibold text-white transition-colors active:translate-y-px disabled:cursor-not-allowed disabled:opacity-60"
      style={{
        height: 32,
        padding: '0 16px',
        borderRadius: 9,
        background: T.primary,
        boxShadow: T.primaryShadow,
        letterSpacing: '-0.005em',
      }}
      onMouseEnter={(e) => {
        if (disabled) return
        e.currentTarget.style.background = T.primaryHover
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = T.primary
      }}
    >
      {icon}
      {label}
    </button>
  )
}

/** 흰 outline 버튼 — 수정 요청/넘어가기/재생성 등 보조 액션. */
export function OutlineActionButton({ onClick, disabled, label, icon }: ActionButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="inline-flex items-center gap-1.5 text-[12.5px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50"
      style={{
        height: 32,
        padding: '0 13px',
        borderRadius: 9,
        background: T.surface,
        border: `1px solid ${T.border}`,
        color: T.ink2,
        letterSpacing: '-0.005em',
      }}
      onMouseEnter={(e) => {
        if (disabled) return
        e.currentTarget.style.background = T.surfaceAlt
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = T.surface
      }}
    >
      {icon}
      {label}
    </button>
  )
}
