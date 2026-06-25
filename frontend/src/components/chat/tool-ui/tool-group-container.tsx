'use client'

import { type ReactNode } from 'react'
import { useTranslations } from 'next-intl'
import { CollapsiblePill } from './collapsible-pill'
import { toolGroupLabelKey } from '@/lib/chat/tool-group-meta'

// ──────────────────────────────────────────────
// ToolGroupContainer — 연속 같은 도구 호출(N≥2)을 1개 컨테이너로 묶는 비주얼.
//
// 그룹핑 로직은 공식 `MessagePrimitive.GroupedParts`가 담당하고, 이 컴포넌트는
// 헤더 라벨("웹 검색 · 10회")과 펼침/접힘 비주얼만 책임진다.
//
// running→done 자동 펼침/접힘은 부모 render fn이 `key={running ? 'running' : 'done'}`
// 로 remount하여 `defaultExpanded`(uncontrolled)를 다시 평가하게 만든다 — React 19
// effect-setState 금지 규칙에 맞춘 프로젝트 표준 패턴(AGENTS.md).
// ──────────────────────────────────────────────

interface ToolGroupContainerProps {
  /** 그룹된 도구명. 라벨 매핑이 없으면 toolName 자체를 라벨로 쓴다. */
  readonly toolName: string
  /** 그룹 안의 호출 횟수(N). */
  readonly count: number
  /** 그룹 내 호출이 하나라도 진행 중인지. true면 펼친 상태로 표시. */
  readonly running: boolean
  /** 그룹 내부의 개별 도구 호출 UI(공식 GroupedParts가 렌더한 subtree). */
  readonly children: ReactNode
}

export function ToolGroupContainer({
  toolName,
  count,
  running,
  children,
}: ToolGroupContainerProps) {
  const t = useTranslations('chat.toolGroup')
  const key = toolGroupLabelKey(toolName)
  const label = key ? t(`labels.${key}`) : toolName

  return (
    <CollapsiblePill
      kind="tool"
      status={running ? 'loading' : 'success'}
      title={label}
      meta={t('count', { count })}
      defaultExpanded={running}
    >
      {children}
    </CollapsiblePill>
  )
}
