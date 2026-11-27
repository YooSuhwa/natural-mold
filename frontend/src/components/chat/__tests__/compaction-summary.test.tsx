import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiFetch } from '@/lib/api/client'
import { render, screen, userEvent } from '../../../../tests/test-utils'
import { CompactionSummary } from '../compaction-summary'

vi.mock('@/lib/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/client')>()),
  apiFetch: vi.fn(),
}))

const evidence = {
  conversationId: '427685b2-34d2-4071-974e-e2c225e0e895',
  checkpointId: '543a1cf1-2a4a-4d72-bf09-877be17110cc',
  messageId: 'assistant-25',
  historyId: 'history_444455556666777788889999',
}
const digest = 'a'.repeat(64)
const endpoint = `/api/conversations/${evidence.conversationId}/history-evidence/${evidence.checkpointId}`
const unavailable = '이 요약에 연결된 대화를 확인할 수 없어요.'
const fetchMock = vi.mocked(apiFetch)

function evidencePage(text: string, nextOffset: number | null = null) {
  return {
    checkpoint_id: evidence.checkpointId,
    history_id: evidence.historyId,
    format: 'jsonl',
    content_sha256: digest,
    byte_length: 1200,
    excerpts: [{ role: 'assistant', text, truncated: false }],
    next_offset: nextOffset,
  }
}

function requestAt(index: number) {
  const call = fetchMock.mock.calls[index]
  if (!call) throw new Error(`Missing request at index ${index}`)
  const [path, options] = call
  expect(options?.method ?? 'GET').toBe('GET')
  return new URL(path, 'https://moldy.test')
}

beforeEach(() => {
  fetchMock.mockReset()
})

describe('CompactionSummary', () => {
  it('근거 식별자가 없는 예전 요약에는 조회 버튼이 없다', () => {
    render(<CompactionSummary />)
    expect(screen.getByText('이전 대화를 요약해 컨텍스트를 정리했어요')).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('펼칠 때만 읽고 같은 체크포인트와 해시로 다음 페이지를 조회한다', async () => {
    const firstText = '<img src="/archive-fragment" alt="입력 예시">'
    fetchMock
      .mockResolvedValueOnce(evidencePage(firstText, 10))
      .mockResolvedValueOnce(evidencePage('마지막 발췌문'))
    const user = userEvent.setup()
    const { container } = render(<CompactionSummary evidence={evidence} />)
    expect(fetchMock).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: '요약 전 대화 보기' }))
    expect(await screen.findByText(firstText)).toBeVisible()
    expect(container.querySelector('img')).toBeNull()
    const first = requestAt(0)
    expect(first.pathname).toBe(endpoint)
    expect(first.searchParams.get('expected_sha256')).toBeNull()

    await user.click(screen.getByRole('button', { name: '더 보기' }))
    expect(await screen.findByText('마지막 발췌문')).toBeVisible()
    const second = requestAt(1)
    expect(second.pathname).toBe(endpoint)
    expect(second.searchParams.get('offset')).toBe('10')
    expect(second.searchParams.get('expected_sha256')).toBe(digest)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('원문을 연 뒤 체크포인트가 바뀌면 다시 열기 전까지 새 원문을 요청하지 않는다', async () => {
    fetchMock.mockResolvedValueOnce(evidencePage('이전 분기의 발췌문'))
    const user = userEvent.setup()
    const { rerender } = render(<CompactionSummary evidence={evidence} />)
    await user.click(screen.getByRole('button', { name: '요약 전 대화 보기' }))
    expect(await screen.findByText('이전 분기의 발췌문')).toBeVisible()

    rerender(<CompactionSummary evidence={{ ...evidence, checkpointId: 'next-checkpoint' }} />)

    expect(screen.queryByText('이전 분기의 발췌문')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '요약 전 대화 보기' })).toHaveAttribute(
      'aria-expanded',
      'false',
    )
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it.each([
    { checkpoint_id: '00000000-0000-4000-8000-000000000009' },
    { history_id: 'history_aaaabbbbccccddddeeeeffff' },
  ])('응답 식별자가 달라지면 발췌문을 표시하지 않는다 (%o)', async (changedIdentity) => {
    const text = '다른 분기에서 작성한 답변'
    fetchMock.mockResolvedValueOnce({ ...evidencePage(text), ...changedIdentity })
    const user = userEvent.setup()
    render(<CompactionSummary evidence={evidence} />)
    await user.click(screen.getByRole('button', { name: '요약 전 대화 보기' }))
    expect(await screen.findByText(unavailable)).toBeVisible()
    expect(screen.queryByText(text)).not.toBeInTheDocument()
  })

  it('같은 이전 형식 보관 ID라도 요약 경계가 다르면 본문을 숨긴다', async () => {
    const text = '뒤이은 요약에서 추가된 대화'
    fetchMock.mockResolvedValueOnce({
      ...evidencePage(text),
      format: 'legacy_markdown',
      cutoff_index: 11,
    })
    const user = userEvent.setup()
    render(<CompactionSummary evidence={{ ...evidence, cutoffIndex: 8 }} />)
    await user.click(screen.getByRole('button', { name: '요약 전 대화 보기' }))
    expect(await screen.findByText(unavailable)).toBeVisible()
    expect(screen.queryByText(text)).not.toBeInTheDocument()
  })

  it('다음 페이지의 해시가 달라지면 서로 다른 보관본을 섞어서 표시하지 않는다', async () => {
    fetchMock
      .mockResolvedValueOnce(evidencePage('이전 버전', 10))
      .mockResolvedValueOnce({ ...evidencePage('변경된 버전'), content_sha256: 'b'.repeat(64) })
    const user = userEvent.setup()
    render(<CompactionSummary evidence={evidence} />)
    await user.click(screen.getByRole('button', { name: '요약 전 대화 보기' }))
    expect(await screen.findByText('이전 버전')).toBeVisible()
    await user.click(screen.getByRole('button', { name: '더 보기' }))
    expect(await screen.findByText(unavailable)).toBeVisible()
    expect(screen.queryByText('변경된 버전')).not.toBeInTheDocument()
  })

  it('조회 권한이 회수되면 열어 둔 이전 발췌문도 닫는다', async () => {
    fetchMock
      .mockResolvedValueOnce(evidencePage('열람 권한이 필요한 본문', 10))
      .mockRejectedValueOnce(new Error('access revoked'))
    const user = userEvent.setup()
    render(<CompactionSummary evidence={evidence} />)
    await user.click(screen.getByRole('button', { name: '요약 전 대화 보기' }))
    expect(await screen.findByText('열람 권한이 필요한 본문')).toBeVisible()
    await user.click(screen.getByRole('button', { name: '더 보기' }))
    expect(await screen.findByText(unavailable)).toBeVisible()
    expect(screen.queryByText('열람 권한이 필요한 본문')).not.toBeInTheDocument()
  })

  it('실시간 메시지는 해당 메시지의 서버 체크포인트로만 연결한다', async () => {
    fetchMock
      .mockResolvedValueOnce({
        metadata: {
          checkpoint_by_message_id: {
            'other-message': 'sibling-checkpoint',
            [evidence.messageId]: evidence.checkpointId,
          },
        },
        values: { messages: [{ id: evidence.messageId, type: 'ai' }] },
      })
      .mockResolvedValueOnce(evidencePage('연결된 메시지입니다'))
    const user = userEvent.setup()
    render(<CompactionSummary evidence={{ ...evidence, checkpointId: undefined }} />)
    await user.click(screen.getByRole('button', { name: '요약 전 대화 보기' }))
    expect(await screen.findByText('연결된 메시지입니다')).toBeVisible()
    expect(requestAt(0).pathname).toBe(
      `/api/conversations/${evidence.conversationId}/langgraph/threads/${evidence.conversationId}/state`,
    )
    expect(requestAt(1).pathname).toBe(endpoint)
  })

  it('메시지에 맞는 체크포인트가 없으면 이웃 메시지의 원문을 요청하지 않는다', async () => {
    fetchMock.mockResolvedValueOnce({
      metadata: { checkpoint_by_message_id: { 'other-message': evidence.checkpointId } },
      values: { messages: [{ id: 'other-message', type: 'ai' }] },
    })
    const user = userEvent.setup()
    render(<CompactionSummary evidence={{ ...evidence, checkpointId: undefined }} />)
    await user.click(screen.getByRole('button', { name: '요약 전 대화 보기' }))
    expect(await screen.findByText(unavailable)).toBeVisible()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
