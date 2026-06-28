'use client'

import { useEffect, useRef, useState, type ReactNode } from 'react'
import {
  BrainIcon,
  ChevronDownIcon,
  CircleCheckIcon,
  CircleSlashIcon,
  Loader2Icon,
  UsersIcon,
  WrenchIcon,
  XCircleIcon,
  type LucideIcon,
} from 'lucide-react'
import { cn } from '@/lib/utils'

// ──────────────────────────────────────────────
// CollapsiblePill — tool/subagent/thinking 통일 표현
//
// 4상태(loading/success/error/cancelled) × 3종(tool/subagent/thinking)을
// 하나의 컴포넌트로 그린다. 기존 generic-tool-ui / sub-agent-ui / search-tool-ui /
// plan-tool-ui 등에서 반복되던 헤더 + status 아이콘 + 토글 패턴을 단일화.
// ──────────────────────────────────────────────

export type PillStatus = 'loading' | 'success' | 'error' | 'cancelled'
export type PillKind = 'tool' | 'subagent' | 'thinking'

/**
 * assistant-ui의 ``status.type``을 PillStatus로 매핑하는 표준 헬퍼.
 *
 * 5개 tool-ui 파일에 흩어져 있던 매핑 함수를 통합 (PR #103 review에서 발견된
 * 미스매치). HiTL reject 등의 ``incomplete``는 의미상 cancelled가 정확.
 */
export function pillStatusFromAssistantUi(
  statusType: 'running' | 'complete' | 'incomplete' | 'requires-action' | string | undefined,
): PillStatus {
  if (statusType === 'running' || statusType === 'requires-action') return 'loading'
  if (statusType === 'incomplete') return 'cancelled'
  if (statusType === 'complete') return 'success'
  if (statusType === undefined) return 'loading'
  return 'error'
}

interface CollapsiblePillProps {
  status: PillStatus
  kind?: PillKind
  /** 헤더 좌측의 굵은 라벨 (도구명/서브에이전트명/사고 단계명). */
  title: string
  /** 라벨 우측의 보조 텍스트 또는 카운트 ("검색 중…", "5건" 등). */
  meta?: ReactNode
  /**
   * kind 아이콘 자리에 표시할 커스텀 아이콘. file 도구 종류 구분
   * (FileIcon/FileEditIcon/FilePlusIcon)처럼 같은 ``kind="tool"``이지만
   * 시각적으로 더 좁히고 싶을 때. ``kind`` icon보다 우선 적용된다.
   */
  leadingIcon?: LucideIcon
  /** 확장 시 보일 본문. 미지정 시 chevron 자체를 숨긴다. */
  children?: ReactNode
  /** 확장되기 전까지 만들 필요가 없는 무거운 본문. */
  renderBody?: () => ReactNode
  defaultExpanded?: boolean
  /**
   * Chevron 옆에 추가로 띄울 아이콘 버튼들 (예: 사이드 패널 펼치기).
   * 제목 영역 클릭과 별개로 동작해야 하므로 호출 측에서 stopPropagation 처리.
   */
  trailing?: ReactNode
  /** pill 전체를 버튼으로 쓸 때 (sub-agent 카드처럼). children 없을 때 권장. */
  onClick?: () => void
  className?: string
}

const STATUS_META: Record<
  PillStatus,
  {
    Icon: LucideIcon
    iconClass: string
    /** 컨테이너 보더/배경 변형 (error/cancelled에 약한 틴트). */
    containerClass: string
    /** 회전/스피너 동작이 필요한 상태인지. */
    spin?: boolean
  }
> = {
  loading: {
    Icon: Loader2Icon,
    iconClass: 'text-status-info',
    containerClass: '',
    spin: true,
  },
  success: {
    Icon: CircleCheckIcon,
    iconClass: 'text-status-success',
    containerClass: '',
  },
  error: {
    Icon: XCircleIcon,
    iconClass: 'text-status-danger',
    containerClass: 'border-status-danger/30 bg-status-danger/5',
  },
  cancelled: {
    Icon: CircleSlashIcon,
    iconClass: 'text-muted-foreground',
    containerClass: 'border-border/40 bg-muted/30',
  },
}

const KIND_ICON: Record<PillKind, LucideIcon> = {
  tool: WrenchIcon,
  subagent: UsersIcon,
  thinking: BrainIcon,
}

export function CollapsiblePill({
  status,
  kind,
  title,
  meta,
  leadingIcon,
  children,
  renderBody,
  defaultExpanded = false,
  trailing,
  onClick,
  className,
}: CollapsiblePillProps) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  // `useState` reads `defaultExpanded` only at mount. A subagent card mounts
  // while its discovery snapshot is still being seeded from history hydration
  // (page reload), so `defaultExpanded` starts false and flips true once the
  // snapshot lands. Without re-syncing, the card would stay collapsed on reload
  // even though it auto-expands live — and because the scoped body only mounts
  // (and lazily resolves its messages) when expanded, the subagent's result
  // would never render. Re-sync on the rising edge only, so a user's manual
  // collapse — which does not change `defaultExpanded` — is preserved.
  const prevDefaultExpandedRef = useRef(defaultExpanded)
  useEffect(() => {
    if (defaultExpanded && !prevDefaultExpandedRef.current) setExpanded(true)
    prevDefaultExpandedRef.current = defaultExpanded
  }, [defaultExpanded])
  const { Icon: StatusIcon, iconClass, containerClass, spin } = STATUS_META[status]
  // leadingIcon이 명시되면 그것을 사용, 없으면 kind 매핑 폴백
  const HeaderIcon = leadingIcon ?? (kind ? KIND_ICON[kind] : null)
  const expandable =
    renderBody !== undefined || (children !== undefined && children !== null && children !== false)

  const headerInner = (
    <>
      <StatusIcon className={cn('size-3.5 shrink-0', iconClass, spin && 'animate-spin')} />
      {HeaderIcon ? (
        <HeaderIcon className="size-3 shrink-0 text-muted-foreground" aria-hidden />
      ) : null}
      <span className="truncate font-medium">{title}</span>
      {meta ? <span className="min-w-0 truncate text-muted-foreground">{meta}</span> : null}
    </>
  )

  // pill 전체를 버튼으로: children 없고 onClick만 주어진 케이스
  if (!expandable && onClick) {
    if (trailing) {
      return (
        <div
          className={cn(
            'moldy-tool-pill group flex w-full items-center gap-2 px-3 py-2 text-left text-xs',
            containerClass,
            className,
          )}
        >
          <button
            type="button"
            onClick={onClick}
            className="flex min-w-0 flex-1 items-center gap-2 text-left"
          >
            {headerInner}
          </button>
          {trailing}
        </div>
      )
    }

    return (
      <button
        type="button"
        onClick={onClick}
        className={cn(
          'moldy-tool-pill group flex w-full items-center gap-2 px-3 py-2 text-left text-xs',
          containerClass,
          className,
        )}
      >
        {headerInner}
        {trailing}
      </button>
    )
  }

  return (
    <div className={cn('moldy-tool-pill w-full text-xs', containerClass, className)}>
      <div className="flex w-full items-center gap-2 px-3 py-2">
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
          onClick={() => {
            if (expandable) setExpanded((v) => !v)
            else if (onClick) onClick()
          }}
          disabled={!expandable && !onClick}
        >
          {headerInner}
        </button>
        {trailing}
        {expandable ? (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            aria-label={expanded ? 'Collapse' : 'Expand'}
            aria-expanded={expanded}
            className="inline-flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          >
            <ChevronDownIcon
              className={cn('size-3.5 transition-transform duration-200', expanded && 'rotate-180')}
            />
          </button>
        ) : null}
      </div>
      {expandable && expanded ? (
        <div className="border-t px-3 py-2">{renderBody ? renderBody() : children}</div>
      ) : null}
    </div>
  )
}
