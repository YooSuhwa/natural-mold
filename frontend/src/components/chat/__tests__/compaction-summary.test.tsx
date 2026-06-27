import { describe, expect, it, vi } from 'vitest'
import { fireEvent } from '@testing-library/react'
import { render, screen } from '../../../../tests/test-utils'
import { CompactionSummary } from '../compaction-summary'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

describe('CompactionSummary', () => {
  it('offloadPath가 없으면 요약 문구만 보이고 원본 보기는 없다', () => {
    render(<CompactionSummary />)

    expect(screen.getByText('이전 대화를 요약해 컨텍스트를 정리했어요')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '원본 보기' })).not.toBeInTheDocument()
  })

  it('offloadPath가 있으면 원본 보기 클릭 시 경로를 클립보드에 복사한다', () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })

    render(<CompactionSummary offloadPath="/conversation_history/t.md" />)
    fireEvent.click(screen.getByRole('button', { name: '원본 보기' }))

    expect(writeText).toHaveBeenCalledWith('/conversation_history/t.md')
  })
})
