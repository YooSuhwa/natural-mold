import { ApprovalDefaultControl } from '@/features/tool-approval/components/approval-default-control'
import { McpToolTable } from '@/app/mcp-servers/_components/mcp-tool-table'
import type { ToolApprovalToolV1 } from '@/lib/types/runtime-policy'
import { render, screen, userEvent, waitFor } from '../../test-utils'

function setup(value: ToolApprovalToolV1 | null, onChange = vi.fn()) {
  render(
    <ApprovalDefaultControl
      id="tool-1"
      label="승인 기본값"
      noneLabel="기본값 없음"
      value={value}
      onChange={onChange}
    />,
  )
  return { onChange, select: screen.getByLabelText('승인 기본값') as HTMLSelectElement }
}

describe('ApprovalDefaultControl', () => {
  it('shows the saved default and sends a plain mode at once', async () => {
    const { onChange, select } = setup({ mode: 'always' })
    expect(select.value).toBe('always')

    await userEvent.selectOptions(select, 'never')
    expect(onChange).toHaveBeenCalledWith({ mode: 'never' })

    await userEvent.selectOptions(select, 'none')
    expect(onChange).toHaveBeenLastCalledWith(null)
  })

  it('holds a condition until it names an argument', async () => {
    const { onChange, select } = setup(null)
    expect(select.value).toBe('none')

    await userEvent.selectOptions(select, 'when')
    expect(onChange).not.toHaveBeenCalled()
    expect(select.value).toBe('when')

    await userEvent.type(screen.getByLabelText('인자 이름'), 'to')
    await userEvent.tab()
    expect(onChange).toHaveBeenCalledWith({
      mode: 'when',
      rule: { all: [{ source: 'args', path: '/to', op: 'exists' }] },
    })
  })

  it('keeps what was sent on screen until the save settles, then follows the server', async () => {
    let settle: () => void = () => {}
    const onChange = vi.fn(() => new Promise<void>((resolve) => (settle = resolve)))
    const { select } = setup({ mode: 'always' }, onChange)

    await userEvent.selectOptions(select, 'never')
    expect(select.value).toBe('never')

    settle()
    // The save failed to change the row: the control is back on the server's value.
    await waitFor(() => expect(select.value).toBe('always'))
  })

  it('is not editable when disabled', () => {
    render(
      <ApprovalDefaultControl
        id="tool-1"
        label="승인 기본값"
        noneLabel="기본값 없음"
        value={{ mode: 'when', rule: { all: [{ source: 'args', path: '/to', op: 'exists' }] } }}
        disabled
        onChange={vi.fn()}
      />,
    )
    expect(screen.getByLabelText('승인 기본값')).toBeDisabled()
    expect(screen.queryByLabelText('인자 이름')).toBeNull()
  })
})

describe('McpToolTable approval defaults', () => {
  const tools = [
    { id: 't1', name: 'send', description: null, approval_default: { mode: 'never' as const } },
    { id: 't2', name: 'read', description: null, approval_default: null },
  ]

  it('offers the default per tool only to those who may set it', async () => {
    const onApprovalDefault = vi.fn()
    const { rerender } = render(<McpToolTable tools={tools} />)
    expect(screen.queryByLabelText('send 승인 기본값')).toBeNull()

    rerender(<McpToolTable tools={tools} onApprovalDefault={onApprovalDefault} />)
    const send = screen.getByLabelText('send 승인 기본값') as HTMLSelectElement
    const read = screen.getByLabelText('read 승인 기본값') as HTMLSelectElement
    expect(send.value).toBe('never')
    expect(read.value).toBe('none')
    expect(read.selectedOptions[0].textContent).toBe('서버 기본값')

    await userEvent.selectOptions(read, 'always')
    expect(onApprovalDefault).toHaveBeenCalledWith('t2', { mode: 'always' })
  })
})
