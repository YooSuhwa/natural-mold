import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ComposerGhostSuggestion } from '../composer-ghost-suggestion'

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string, params?: Record<string, unknown>) =>
    params ? `${key}(${Object.values(params).join('/')})` : key,
}))

describe('ComposerGhostSuggestion', () => {
  it('제안 텍스트와 → 키캡 힌트를 연하게 렌더한다', () => {
    render(<ComposerGhostSuggestion text="방금 답변을 표로 정리해줘" onAccept={() => {}} />)
    expect(screen.getByText('방금 답변을 표로 정리해줘')).toBeInTheDocument()
    expect(screen.getByText('hint')).toBeInTheDocument()
    expect(document.querySelector('[data-moldy-followup-ghost]')).not.toBeNull()
  })

  it('텍스트 클릭 시 수락 콜백을 호출한다 (터치 폴백)', async () => {
    const user = userEvent.setup()
    const onAccept = vi.fn()
    render(<ComposerGhostSuggestion text="표로 정리해줘" onAccept={onAccept} />)
    await user.click(screen.getByText('표로 정리해줘'))
    expect(onAccept).toHaveBeenCalledTimes(1)
  })
})
