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

import { asRecord, isRecord, planMeta, resultMeta, subagentTitle, text } from './chip-values'
import { protocolChips } from './protocol-chips'
import type { ChipInfo } from './chip-values'
import type { LegacyTraceEvent, TraceEvent, TurnTrace } from '@/lib/types/share'

export type { ChipInfo, ChipKind, ChipStatus } from './chip-values'

const _isLegacyEvent = (evt: TraceEvent): evt is LegacyTraceEvent =>
  'event' in evt && typeof evt.event === 'string' && isRecord(evt.data)

function _legacyChips(events: TraceEvent[]): ChipInfo[] {
  const chips: ChipInfo[] = []
  const pendingResults: Map<string, LegacyTraceEvent[]> = new Map()
  for (const evt of events) {
    if (!_isLegacyEvent(evt) || evt.event !== 'tool_call_result') continue
    const name = text(evt.data.tool_name) ?? ''
    if (!name) continue
    const queue = pendingResults.get(name) ?? []
    queue.push(evt)
    pendingResults.set(name, queue)
  }

  for (const evt of events) {
    if (!_isLegacyEvent(evt) || evt.event !== 'tool_call_start') continue
    const toolName = text(evt.data.tool_name) ?? ''
    if (!toolName) continue
    const params = asRecord(evt.data.parameters)
    const queue = pendingResults.get(toolName) ?? []
    const matched = queue.shift()
    pendingResults.set(toolName, queue)

    if (toolName === 'task') {
      chips.push({ kind: 'subagent', status: 'success', title: subagentTitle(params) })
      continue
    }
    if (toolName === 'write_todos') {
      chips.push({ kind: 'tool', status: 'success', title: 'Plan', meta: planMeta(params) })
      continue
    }
    chips.push({
      kind: 'tool',
      status: 'success',
      title: toolName,
      meta: resultMeta(matched?.data.result),
    })
  }
  return chips
}

/**
 * Turn 1건의 events를 chip 배열로 변환. 빈 배열이면 (tool 호출이 없었던 단순
 * 텍스트 응답) 빈 결과를 반환.
 */
export function extractChips(turn: TurnTrace): ChipInfo[] {
  return [..._legacyChips(turn.events), ...protocolChips(turn.events)]
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
