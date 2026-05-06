/**
 * ``hasNewAssistantMessage`` 회귀 가드 — use-chat-runtime 의 streamingMessages
 * 클리어 휴리스틱. PR #132 fix(설명: run_id/uuid4 ↔ messages.id/uuid5 형식
 * 불일치로 답변 두 번 표시) 의 핵심 분기점.
 *
 * 회귀 시나리오:
 * - 정상 종료: 새 assistant id 도착 → 클리어 트리거(true)
 * - mid-stream 끊김: assistant id 도착 없음 → partial 토큰 유지(false)
 * - user 만 추가됨: assistant 미도착 → false (W3-out M5 보호 의도)
 */

import { describe, expect, it } from 'vitest'
import type { Message } from '@/lib/types'
import { hasNewAssistantMessage } from '../use-chat-runtime'

function msg(id: string, role: Message['role'], content = ''): Message {
  return {
    id,
    conversation_id: 'c',
    role,
    content,
    tool_calls: null,
    tool_call_id: null,
    created_at: '2026-01-01T00:00:00Z',
    feedback: null,
    attachments: null,
    usage: null,
    parent_id: null,
    branch_checkpoint_id: null,
    siblings: null,
    sibling_checkpoint_ids: null,
    branch_index: null,
    branch_total: null,
  } as unknown as Message
}

describe('hasNewAssistantMessage', () => {
  it('정상 종료 — 새 assistant id 도착 시 true (streamingMessages 클리어 트리거)', () => {
    const prev = [msg('u-1', 'user', 'hi')]
    const next = [msg('u-1', 'user', 'hi'), msg('a-1', 'assistant', '안녕!')]
    expect(hasNewAssistantMessage(prev, next)).toBe(true)
  })

  it('mid-stream 끊김 — assistant 미도착이면 false (partial 토큰 보존)', () => {
    // backend 가 user 메시지만 저장하고 assistant 는 checkpointer commit 못 한 상태.
    const prev: Message[] = []
    const next = [msg('u-1', 'user', 'hi')]
    expect(hasNewAssistantMessage(prev, next)).toBe(false)
  })

  it('user 만 새로 추가 — false (W3-out M5 보호)', () => {
    const prev = [msg('a-prev', 'assistant', '이전 응답')]
    const next = [msg('a-prev', 'assistant', '이전 응답'), msg('u-1', 'user', 'hi')]
    expect(hasNewAssistantMessage(prev, next)).toBe(false)
  })

  it('변경 없음 — 동일 messages 면 false', () => {
    const m = [msg('u-1', 'user', 'hi'), msg('a-1', 'assistant', 'ok')]
    expect(hasNewAssistantMessage(m, m)).toBe(false)
  })

  it('assistant id 가 prev set 에 이미 있으면 false (재 fetch 됐으나 새 turn 아님)', () => {
    const a = msg('a-1', 'assistant', 'ok')
    const prev = [msg('u-1', 'user', 'hi'), a]
    const next = [msg('u-1', 'user', 'hi'), a, msg('u-2', 'user', 'q2')]
    expect(hasNewAssistantMessage(prev, next)).toBe(false)
  })

  it('multi-turn — assistant 1 + tool n + assistant 2 가 한 번에 도착해도 true', () => {
    const prev = [msg('u-1', 'user', 'q')]
    const next = [
      msg('u-1', 'user', 'q'),
      msg('a-1', 'assistant', 'thinking'),
      msg('t-1', 'tool', 'tool result'),
      msg('a-2', 'assistant', 'final'),
    ]
    expect(hasNewAssistantMessage(prev, next)).toBe(true)
  })
})
