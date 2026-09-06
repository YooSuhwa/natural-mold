import { describe, expect, it } from 'vitest'
import { render, screen } from '../../../../tests/test-utils'
import { CompactionSummary } from '../compaction-summary'

describe('CompactionSummary', () => {
  it('요약 문구만 보이고 원본 보기나 복사 동작은 제공하지 않는다', () => {
    render(<CompactionSummary />)

    expect(screen.getByText('이전 대화를 요약해 컨텍스트를 정리했어요')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '원본 보기' })).not.toBeInTheDocument()
  })
})
