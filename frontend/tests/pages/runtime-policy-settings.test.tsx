import { useState } from 'react'
import { render, screen, userEvent } from '../test-utils'
import {
  RuntimePolicySettings,
  type RuntimePolicySettingsProps,
} from '@/components/agent/runtime-policy-settings'
import type { RuntimePolicyV1 } from '@/lib/types/runtime-policy'

const DEFAULT_PROPS: RuntimePolicySettingsProps = {
  value: null,
  onValueChange: vi.fn(),
  contextWindow: 128_000,
  surface: 'existing-agent',
}

function ControlledRuntimePolicySettings(
  props: Omit<RuntimePolicySettingsProps, 'value' | 'onValueChange'> & {
    readonly initialValue?: RuntimePolicyV1 | null
  },
) {
  const [value, setValue] = useState<RuntimePolicyV1 | null>(props.initialValue ?? null)

  return <RuntimePolicySettings {...props} value={value} onValueChange={setValue} />
}

describe('RuntimePolicySettings', () => {
  it('creates the exact v1 custom policy from the recommended state', async () => {
    const user = userEvent.setup()
    const onValueChange = vi.fn()

    render(<RuntimePolicySettings {...DEFAULT_PROPS} onValueChange={onValueChange} />)

    await user.click(screen.getByRole('button', { name: '직접 설정' }))

    expect(onValueChange).toHaveBeenCalledWith({
      version: 1,
      filesystem: { mode: 'artifact_write' },
      todo: { enabled: true },
      summarization: { mode: 'auto' },
    })
  })

  it('resets a custom policy to the recommended state', async () => {
    const user = userEvent.setup()
    const onValueChange = vi.fn()

    render(
      <RuntimePolicySettings
        {...DEFAULT_PROPS}
        onValueChange={onValueChange}
        value={{
          version: 1,
          filesystem: { mode: 'inspect' },
          todo: { enabled: false },
          summarization: { mode: 'auto' },
        }}
      />,
    )

    await user.click(screen.getByRole('button', { name: '권장 설정 사용' }))

    expect(onValueChange).toHaveBeenCalledWith(null)
  })

  it('keeps an existing custom policy when custom is already selected', async () => {
    const user = userEvent.setup()
    const onValueChange = vi.fn()

    render(
      <RuntimePolicySettings
        {...DEFAULT_PROPS}
        onValueChange={onValueChange}
        value={{
          version: 1,
          filesystem: { mode: 'inspect' },
          todo: { enabled: false },
          summarization: { mode: 'preset', preset: 'balanced_context_v1' },
        }}
      />,
    )

    await user.click(screen.getByRole('button', { name: '직접 설정' }))

    expect(onValueChange).not.toHaveBeenCalled()
  })

  it('updates custom filesystem, todo, and summarization controls', async () => {
    const user = userEvent.setup()

    render(<ControlledRuntimePolicySettings {...DEFAULT_PROPS} />)

    await user.click(screen.getByRole('button', { name: '직접 설정' }))
    await user.click(screen.getByRole('button', { name: '검토만' }))
    await user.click(screen.getByRole('switch', { name: '할 일 목록 사용' }))
    await user.click(screen.getByRole('button', { name: '균형' }))

    expect(screen.getByRole('button', { name: '검토만' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('switch', { name: '할 일 목록 사용' })).toHaveAttribute(
      'aria-checked',
      'false',
    )
    expect(screen.getByRole('button', { name: '균형' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('groups each mutually exclusive runtime choice under its visible purpose', async () => {
    const user = userEvent.setup()

    render(<ControlledRuntimePolicySettings {...DEFAULT_PROPS} />)

    expect(screen.getByRole('group', { name: '실행 동작 설정 방식' })).toContainElement(
      screen.getByRole('button', { name: '권장 설정 사용' }),
    )

    await user.click(screen.getByRole('button', { name: '직접 설정' }))

    expect(screen.getByRole('group', { name: '파일 작업' })).toContainElement(
      screen.getByRole('button', { name: '검토만' }),
    )
    expect(screen.getByRole('group', { name: '긴 대화 정리' })).toContainElement(
      screen.getByRole('button', { name: '자동' }),
    )
  })

  it('disables balanced summarization without a positive context window', async () => {
    const user = userEvent.setup()

    render(<ControlledRuntimePolicySettings {...DEFAULT_PROPS} contextWindow={null} />)

    await user.click(screen.getByRole('button', { name: '직접 설정' }))

    expect(screen.getByRole('button', { name: '균형' })).toBeDisabled()
    expect(
      screen.getByText('선택한 모델의 컨텍스트 길이 정보가 있어야 균형 모드를 사용할 수 있어요.'),
    ).toBeInTheDocument()
  })

  it('warns about an already invalid balanced policy without replacing it', () => {
    const onValueChange = vi.fn()

    render(
      <RuntimePolicySettings
        {...DEFAULT_PROPS}
        contextWindow={0}
        onValueChange={onValueChange}
        value={{
          version: 1,
          filesystem: { mode: 'artifact_write' },
          todo: { enabled: true },
          summarization: { mode: 'preset', preset: 'balanced_context_v1' },
        }}
      />,
    )

    expect(screen.getByRole('button', { name: '균형' })).toBeDisabled()
    expect(
      screen.getByText(
        '현재 선택은 이 모델에서 사용할 수 없습니다. 모델을 바꾸거나 자동으로 전환하세요.',
      ),
    ).toBeInTheDocument()
    expect(onValueChange).not.toHaveBeenCalled()
  })

  it('uses a closed advanced presentation only for manual creation', async () => {
    const user = userEvent.setup()

    render(<RuntimePolicySettings {...DEFAULT_PROPS} surface="new-agent" collapsible />)

    expect(screen.getByRole('button', { name: '직접 설정' })).not.toBeVisible()
    await user.click(screen.getByText('실행 동작 고급 설정'))
    expect(screen.getByRole('button', { name: '직접 설정' })).toBeInTheDocument()
    expect(screen.getByText('새 대화부터 설정이 적용됩니다.')).toBeInTheDocument()
  })

  it('labels the source without exposing internal runtime provenance', () => {
    render(<RuntimePolicySettings {...DEFAULT_PROPS} />)

    expect(screen.getByText('권장 설정')).toBeInTheDocument()
    expect(
      screen.getByText(
        '이미 실행된 대화에는 영향을 주지 않습니다. 아직 실행하지 않은 대화와 새 대화에는 변경된 설정이 적용됩니다.',
      ),
    ).toBeInTheDocument()
    expect(screen.queryByText(/legacy_compat|stored/i)).not.toBeInTheDocument()
  })
})
