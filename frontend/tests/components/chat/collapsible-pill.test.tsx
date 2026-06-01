import { render, screen, userEvent } from '../../test-utils'
import { CollapsiblePill } from '@/components/chat/tool-ui/collapsible-pill'
import { ToolFallbackPanel } from '@/components/chat/tool-ui/generic-tool-ui'

describe('CollapsiblePill', () => {
  it('does not render lazy body content while collapsed', async () => {
    const renderBody = vi.fn(() => <div>무거운 결과</div>)

    render(
      <CollapsiblePill
        kind="tool"
        status="success"
        title="execute_in_skill"
        renderBody={renderBody}
      />,
    )

    expect(renderBody).not.toHaveBeenCalled()
    expect(screen.queryByText('무거운 결과')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Expand' }))

    expect(renderBody).toHaveBeenCalledTimes(1)
    expect(screen.getByText('무거운 결과')).toBeInTheDocument()
  })

  it('keeps heavy tool results lazy until the panel is opened', async () => {
    const circular: Record<string, unknown> = { ok: true }
    circular.self = circular

    render(
      <ToolFallbackPanel
        toolName="large_result_tool"
        args={{ city: '울산' }}
        result={circular}
        status="complete"
      />,
    )

    expect(screen.getByText('large_result_tool')).toBeInTheDocument()
    expect(screen.queryByText('ok')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Expand' }))

    expect(screen.getByText(/직렬화할 수 없는 결과/)).toBeInTheDocument()
  })

  it('memoizes heavy tool value formatting while expanded for stable payloads', async () => {
    const args = { city: '울산' }
    const result = { ok: true, items: Array.from({ length: 10 }, (_, index) => ({ index })) }
    const stringifySpy = vi.spyOn(JSON, 'stringify')

    const { rerender } = render(
      <ToolFallbackPanel
        toolName="large_result_tool"
        args={args}
        result={result}
        status="complete"
      />,
    )

    await userEvent.click(screen.getByRole('button', { name: 'Expand' }))
    const stringifyCallsAfterExpand = stringifySpy.mock.calls.length

    rerender(
      <ToolFallbackPanel
        toolName="large_result_tool"
        args={args}
        result={result}
        status="complete"
      />,
    )

    expect(stringifySpy).toHaveBeenCalledTimes(stringifyCallsAfterExpand)
  })
})
