'use client'

import type { BaseMessage } from '@langchain/core/messages'

/**
 * 합성 terminal-notice 버블(취소/stale/실패)이 ``additional_kwargs.metadata``에
 * 싣는 판별 키. ``convertMoldyLangChainMessage``가 이 값을 ``metadata.custom``
 * (``terminalNotice``)으로 승격해 ``AssistantMessage`` 렌더가 읽는다. 특히
 * ``failed``는 에러 스타일 + retry 버튼(G2)을 트리거한다.
 *
 * compaction/usage와 동일하게 additional_kwargs.metadata 규약을 쓴다 —
 * ``convertLangChainBaseMessage``는 additional_kwargs를 custom으로 자동
 * 승격하지 않으므로 명시적 attach가 필요하다.
 */
export const TERMINAL_NOTICE_METADATA_KEY = 'moldy_terminal_notice'

export type TerminalNoticeStatus = 'canceled' | 'canceling' | 'stale' | 'failed'

const TERMINAL_NOTICE_STATUSES: readonly TerminalNoticeStatus[] = [
  'canceled',
  'canceling',
  'stale',
  'failed',
]

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function isTerminalNoticeStatus(value: unknown): value is TerminalNoticeStatus {
  return (
    typeof value === 'string' && TERMINAL_NOTICE_STATUSES.includes(value as TerminalNoticeStatus)
  )
}

/** 합성 terminal-notice 버블의 메시지 id 판별. ``appendTerminalRunNotice``가
 *  ``moldy-<status>-<runId>`` 형태로 만든다. checkpoint fork 계산(regenerate/
 *  retry/edit)은 이 합성 버블을 실제 assistant 턴으로 오인하면 안 되므로 —
 *  특히 실패 버블은 checkpoint가 없어 fork 대상 탐색을 null로 끝내버린다 —
 *  fork context의 visible 메시지에서 제외해야 한다(G2 retry). */
export function isTerminalNoticeMessageId(id: string | undefined | null): boolean {
  if (!id) return false
  return TERMINAL_NOTICE_STATUSES.some((status) => id.startsWith(`moldy-${status}-`))
}

/** 합성 버블 메시지에서 terminal-notice 상태를 추출한다
 *  (compactionFromMessage와 동일한 additional_kwargs.metadata 규약). */
export function terminalNoticeFromMessage(message: BaseMessage): TerminalNoticeStatus | null {
  const additionalKwargs = (message as { additional_kwargs?: unknown }).additional_kwargs
  const metadata =
    isRecord(additionalKwargs) && isRecord(additionalKwargs.metadata)
      ? additionalKwargs.metadata
      : null
  const status = metadata?.[TERMINAL_NOTICE_METADATA_KEY]
  return isTerminalNoticeStatus(status) ? status : null
}
