import type { ReactNode } from 'react'
import type { EnrichedPartState, PartState } from '@assistant-ui/react'
import { isGroupableTool } from '@/lib/chat/tool-group-meta'

// ──────────────────────────────────────────────
// 공유 tool-call 그룹핑 헬퍼.
//
// 메인 v3 채팅(`assistant-thread.tsx`)과 빌더 렌더(`builder-overrides.tsx`)가
// 동일한 `MessagePrimitive.GroupedParts` groupBy/노드 판별을 공유한다. 각 표면의
// leaf 렌더(텍스트/도구 박스 비주얼)는 서로 달라 render fn은 표면별로 두지만,
// "어떤 part를 어떤 group key로 묶는가"와 "group 노드를 판별/해석하는 법"은
// 한 곳에서 정의해 두 표면이 어긋나지 않게 한다.
// ──────────────────────────────────────────────

export const GROUP_TOOL_PREFIX = 'group-tool:'

/**
 * groupBy: tool-call이고 그룹 대상이면 `group-tool:<toolName>` 단일 경로, 아니면 null.
 * key에 toolName을 포함해 "연속 같은 도구"만 합쳐지고, 인접한 다른 도구는 분리된다.
 *
 * 모듈 레벨 const로 둬서 assistant-ui 내부 메모화(identity 기반)가 매 토큰마다
 * 깨지지 않게 한다 — 매 render에서 새 함수를 만들면 streaming 표시가 불안정해진다.
 */
export function groupAssistantParts(part: PartState): readonly [`group-${string}`] | null {
  if (part.type === 'tool-call' && isGroupableTool(part.toolName)) {
    return [`${GROUP_TOOL_PREFIX}${part.toolName}` as `group-${string}`]
  }
  return null
}

/** GroupedParts가 합성하는 group-tool 노드. status는 마지막 part 상태를 미러. */
export type GroupToolNode = {
  readonly type: `group-${string}`
  readonly status?: { type?: string }
  readonly indices: readonly number[]
}

/** GroupedParts render fn이 받는 노드 — group 노드 / leaf part / indicator 중 하나. */
export type GroupedRenderInfo = {
  readonly part: GroupToolNode | EnrichedPartState | { readonly type: 'indicator' }
  readonly children: ReactNode
}

/** part가 group-tool 노드인지(= GROUP_TOOL_PREFIX로 시작하는 합성 type인지). */
export function isGroupToolNode(part: { readonly type: string }): part is GroupToolNode {
  return part.type.startsWith(GROUP_TOOL_PREFIX)
}

/** group-tool 노드의 type에서 원래 toolName을 복원. */
export function groupToolName(node: GroupToolNode): string {
  return node.type.slice(GROUP_TOOL_PREFIX.length)
}
