import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '../../../../tests/test-utils'

const mocks = vi.hoisted(() => ({
  isDictating: false,
  stopDictation: vi.fn(),
}))

vi.mock('@assistant-ui/react', () => ({
  ComposerPrimitive: {
    Dictate: ({ children }: { children: ReactNode }) => children,
    DictationTranscript: () => <>partial transcript</>,
    StopDictation: ({ children }: { children: ReactNode }) => children,
  },
  useAui: () => ({ composer: { stopDictation: mocks.stopDictation } }),
  useAuiState: (selector: (state: unknown) => unknown) =>
    selector({ composer: { dictation: mocks.isDictating ? {} : null } }),
}))

import { ComposerDictationControl } from '../composer-dictation-control'

describe('ComposerDictationControl', () => {
  beforeEach(() => {
    mocks.isDictating = false
    vi.clearAllMocks()
  })

  it('requires an explicit microphone button click before beginning dictation', async () => {
    const onStart = vi.fn()
    const user = (await import('@testing-library/user-event')).default.setup()
    render(<ComposerDictationControl availability="ready" onStart={onStart} />)

    expect(onStart).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '음성 입력 시작' }))

    expect(onStart).toHaveBeenCalledOnce()
  })

  it('explains unsupported and failed browser speech states', () => {
    const { rerender } = render(
      <ComposerDictationControl availability="unsupported" onStart={vi.fn()} />,
    )

    expect(screen.getByText('이 브라우저에서는 음성 입력을 지원하지 않습니다.')).toBeVisible()

    rerender(<ComposerDictationControl availability="failed" onStart={vi.fn()} />)

    expect(
      screen.getByText(
        '음성 입력을 시작하지 못했습니다. 마이크 권한과 브라우저 상태를 확인한 뒤 다시 시도하세요.',
      ),
    ).toBeVisible()
  })

  it('stops dictation when the active thread changes or the composer unmounts', () => {
    const { rerender, unmount } = render(
      <ComposerDictationControl availability="ready" focusKey="conversation-1" onStart={vi.fn()} />,
    )

    rerender(
      <ComposerDictationControl availability="ready" focusKey="conversation-2" onStart={vi.fn()} />,
    )
    expect(mocks.stopDictation).toHaveBeenCalledTimes(1)

    unmount()
    expect(mocks.stopDictation).toHaveBeenCalledTimes(2)
  })
})
