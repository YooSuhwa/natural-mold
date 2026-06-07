/**
 * W6 — turn별 SSE 이벤트 시퀀스에서 공개 페이지 칩으로 표시할 정보를 추출.
 *
 * 입력: ``TurnTrace`` 한 건의 events (``message_start`` … ``message_end``).
 * 출력: ``ChipInfo[]`` — assistant 메시지 본문 위에 떠 있는 도구/서브에이전트
 *      배지 리스트.
 *
 * 추출 규칙:
 * - ``tool_call_start`` 발견 → 도구 chip 시작 (status="success", kind="tool")
 *   - 같은 ``tool_name``의 ``tool_call_result``가 뒤에 있으면 매칭 (가장 가까운
 *     하나, 일대일). 결과 미도착이면 chip은 그대로 success 처리 (스트림이
 *     이미 종료된 상태라 loading 의미 없음).
 *   - 도구명이 ``task`` 면 서브에이전트 chip으로 변환 (kind="subagent",
 *     이름은 ``args.agent_name`` 또는 ``args.subagent_type`` 폴백).
 *   - 도구명이 ``write_todos`` 면 plan chip — 이름 "Plan", meta는 todos 개수.
 * - 기타 이벤트는 무시 (content_delta 등은 본문 텍스트로 이미 노출).
 */

import type { TraceEvent, TurnTrace } from '@/lib/types/share'

export type ChipKind = 'tool' | 'subagent' | 'thinking'
export type ChipStatus = 'loading' | 'success' | 'error' | 'cancelled'

export interface ChipInfo {
  kind: ChipKind
  status: ChipStatus
  title: string
  /** 보조 라벨 — 도구는 결과 길이/개수, plan은 todos 카운트 등. */
  meta?: string
}

interface ToolCallStartData {
  tool_name?: string
  parameters?: Record<string, unknown>
}

interface SubagentArgs {
  agent_name?: string
  subagent_type?: string
}

interface TodoItem {
  status?: 'completed' | 'in_progress' | 'pending'
}

interface WriteTodosArgs {
  todos?: TodoItem[]
  items?: TodoItem[]
}

function _asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
}

function _toolCallStartName(data: Record<string, unknown>): string {
  const v = (data as ToolCallStartData).tool_name
  return typeof v === 'string' && v.length > 0 ? v : ''
}

function _toolCallResultName(data: Record<string, unknown>): string {
  const v = data.tool_name
  return typeof v === 'string' && v.length > 0 ? v : ''
}

function _subagentTitle(parameters: Record<string, unknown>): string {
  const args = parameters as SubagentArgs
  return args.agent_name || args.subagent_type || 'Sub-agent'
}

function _planMeta(parameters: Record<string, unknown>): string | undefined {
  const args = parameters as WriteTodosArgs
  const items = args.todos ?? args.items ?? []
  if (items.length === 0) return undefined
  const completed = items.filter((it) => it?.status === 'completed').length
  return `${completed}/${items.length}`
}

/**
 * Turn 1건의 events를 chip 배열로 변환. 빈 배열이면 (tool 호출이 없었던 단순
 * 텍스트 응답) 빈 결과를 반환.
 */
export function extractChips(turn: TurnTrace): ChipInfo[] {
  const chips: ChipInfo[] = []
  // ``tool_call_result``는 매칭 시점에 소비. 같은 도구 이름이 여러 번 호출
  // 되면 시간순으로 1:1 매칭되도록 큐 형태로 관리.
  const pendingResults: Map<string, TraceEvent[]> = new Map()
  for (const evt of turn.events) {
    if (evt.event === 'tool_call_result') {
      const name = _toolCallResultName(evt.data)
      if (!name) continue
      const queue = pendingResults.get(name) ?? []
      queue.push(evt)
      pendingResults.set(name, queue)
    }
  }

  for (const evt of turn.events) {
    if (evt.event !== 'tool_call_start') continue
    const data = _asRecord(evt.data)
    const toolName = _toolCallStartName(data)
    if (!toolName) continue
    const params = _asRecord(data.parameters)

    // 결과 매칭 (FIFO)
    const queue = pendingResults.get(toolName) ?? []
    const matched = queue.shift()
    pendingResults.set(toolName, queue)

    if (toolName === 'task') {
      chips.push({
        kind: 'subagent',
        status: 'success',
        title: _subagentTitle(params),
      })
      continue
    }

    if (toolName === 'write_todos') {
      chips.push({
        kind: 'tool',
        status: 'success',
        title: 'Plan',
        meta: _planMeta(params),
      })
      continue
    }

    // 일반 도구: 결과 길이를 meta로 (참고용 — null이면 표시 X)
    const result = matched?.data?.result
    const resultStr = typeof result === 'string' ? result : ''
    const meta = resultStr ? `${resultStr.length} chars` : undefined

    chips.push({
      kind: 'tool',
      status: 'success',
      title: toolName,
      meta,
    })
  }

  return chips
}

/**
 * assistant 메시지 id에 매칭되는 turn을 traces 배열에서 찾는다. 백엔드의
 * ``MessageResponse.id``는 raw langchain id 또는 deterministic uuid5 — 그래도
 * ``TurnTrace.assistant_msg_id``(stream_agent_response의 msg_id)와 동일 문자열
 * 비교가 성립하는 경우가 가장 흔한 경로. 매칭 안 되면 null 반환 → 칩 미표시.
 */
export function findTurnForMessage(traces: TurnTrace[], messageId: string): TurnTrace | null {
  for (const t of traces) {
    if (t.assistant_msg_id === messageId) return t
  }
  return null
}
