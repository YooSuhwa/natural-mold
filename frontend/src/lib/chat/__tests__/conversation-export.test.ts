import { describe, expect, it } from 'vitest'
import type { Message, MessagesEnvelope } from '@/lib/types'
import {
  conversationToJson,
  conversationToMarkdown,
  exportFilename,
  type ExportLabels,
} from '../conversation-export'

const labels: ExportLabels = {
  roleUser: '사용자',
  roleAssistant: '어시스턴트',
  roleTool: '도구',
  toolCalls: '도구 호출',
  attachments: '첨부',
  exportedAt: '내보낸 시각',
}

function msg(partial: Partial<Message>): Message {
  return {
    id: 'm1',
    conversation_id: 'c1',
    role: 'user',
    content: '',
    tool_calls: null,
    tool_call_id: null,
    created_at: '2026-07-02T00:00:00Z',
    ...partial,
  } as Message
}

describe('conversationToMarkdown', () => {
  it('제목/헤더/role 라벨/content를 렌더한다', () => {
    const md = conversationToMarkdown(
      [
        msg({ role: 'user', content: '안녕' }),
        msg({ id: 'm2', role: 'assistant', content: '반가워요' }),
      ],
      { title: '테스트 대화', exportedAt: '2026-07-02T00:00:00Z', labels },
    )
    expect(md).toContain('# 테스트 대화')
    expect(md).toContain('## 사용자 · 2026-07-02T00:00:00Z')
    expect(md).toContain('안녕')
    expect(md).toContain('## 어시스턴트')
    expect(md).toContain('반가워요')
  })

  it('tool_calls와 attachments를 렌더한다', () => {
    const md = conversationToMarkdown(
      [
        msg({
          role: 'assistant',
          content: '',
          tool_calls: [{ name: 'web_search', args: { q: 'hi' } }],
          attachments: [
            {
              id: 'a1',
              filename: 'file.png',
              mime_type: 'image/png',
              size_bytes: 1,
              url: 'https://x/f.png',
            },
          ],
        }),
      ],
      { title: 't', exportedAt: 'now', labels },
    )
    expect(md).toContain('web_search')
    expect(md).toContain('[file.png](https://x/f.png)')
  })
})

describe('conversationToJson', () => {
  it('envelope를 파싱 가능한 JSON으로 직렬화한다', () => {
    const envelope = { messages: [msg({ content: 'hi' })] } as MessagesEnvelope
    const json = conversationToJson(envelope)
    expect(JSON.parse(json).messages[0].content).toBe('hi')
  })
})

describe('exportFilename', () => {
  it('conversation-{id}-{ts}.{ext} 형식을 만든다', () => {
    expect(exportFilename('c1', 'md', '2026-07-02T00-00-00')).toBe(
      'conversation-c1-2026-07-02T00-00-00.md',
    )
    expect(exportFilename('c1', 'json', '2026-07-02T00-00-00')).toBe(
      'conversation-c1-2026-07-02T00-00-00.json',
    )
  })
})
