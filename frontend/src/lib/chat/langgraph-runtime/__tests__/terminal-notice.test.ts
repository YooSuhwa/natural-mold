import { AIMessage, HumanMessage } from '@langchain/core/messages'
import { describe, expect, it } from 'vitest'
import {
  TERMINAL_NOTICE_METADATA_KEY,
  isTerminalNoticeMessageId,
  isTerminalNoticeStatus,
  terminalNoticeFromMessage,
} from '../terminal-notice'

function noticeBubble(status: string): AIMessage {
  return new AIMessage({
    id: `moldy-${status}-run1`,
    content: '알림 텍스트',
    additional_kwargs: { metadata: { [TERMINAL_NOTICE_METADATA_KEY]: status } },
  })
}

describe('terminalNoticeFromMessage', () => {
  it('취소/중단/stale/실패 상태를 추출한다', () => {
    expect(terminalNoticeFromMessage(noticeBubble('failed'))).toBe('failed')
    expect(terminalNoticeFromMessage(noticeBubble('canceled'))).toBe('canceled')
    expect(terminalNoticeFromMessage(noticeBubble('canceling'))).toBe('canceling')
    expect(terminalNoticeFromMessage(noticeBubble('stale'))).toBe('stale')
  })

  it('알 수 없는 상태나 일반 메시지는 null을 반환한다', () => {
    expect(terminalNoticeFromMessage(noticeBubble('running'))).toBeNull()
    expect(terminalNoticeFromMessage(new AIMessage({ content: '안녕' }))).toBeNull()
    expect(terminalNoticeFromMessage(new HumanMessage({ content: '안녕' }))).toBeNull()
  })

  it('additional_kwargs.metadata가 없어도 안전하게 null을 반환한다', () => {
    const bare = new AIMessage({ content: '본문' })
    // additional_kwargs를 비워도 크래시 없이 null.
    expect(terminalNoticeFromMessage(bare)).toBeNull()
  })
})

describe('isTerminalNoticeStatus', () => {
  it('알려진 상태만 좁힌다', () => {
    expect(isTerminalNoticeStatus('failed')).toBe(true)
    expect(isTerminalNoticeStatus('stale')).toBe(true)
    expect(isTerminalNoticeStatus('completed')).toBe(false)
    expect(isTerminalNoticeStatus(undefined)).toBe(false)
    expect(isTerminalNoticeStatus(123)).toBe(false)
  })
})

describe('isTerminalNoticeMessageId', () => {
  it('합성 notice 버블 id(moldy-<status>-<runId>)를 판별한다', () => {
    expect(isTerminalNoticeMessageId('moldy-failed-run1')).toBe(true)
    expect(isTerminalNoticeMessageId('moldy-canceled-run1')).toBe(true)
    expect(isTerminalNoticeMessageId('moldy-canceling-x')).toBe(true)
    expect(isTerminalNoticeMessageId('moldy-stale-abc')).toBe(true)
  })

  it('실제 메시지 id·다른 합성 id·빈 값은 false다 (fork 대상 오인 방지)', () => {
    // 실제 assistant 턴은 제외하면 안 된다 — checkpoint fork 대상이므로.
    expect(isTerminalNoticeMessageId('run-abc-123')).toBe(false)
    expect(isTerminalNoticeMessageId('moldy-compaction-1')).toBe(false)
    expect(isTerminalNoticeMessageId(undefined)).toBe(false)
    expect(isTerminalNoticeMessageId(null)).toBe(false)
  })
})
