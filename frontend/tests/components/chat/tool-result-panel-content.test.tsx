import { render } from '../../test-utils'
import { ToolResultPanelContent } from '@/components/chat/right-rail/tool-result-panel-content'

describe('ToolResultPanelContent', () => {
  it('memoizes parsed and pretty-printed JSON for stable payloads', () => {
    const payload = {
      toolCallId: 'tc-1',
      toolName: 'json_tool',
      result: '{"ok":true,"items":[1,2,3]}',
      status: 'complete' as const,
    }
    const parseSpy = vi.spyOn(JSON, 'parse')
    const stringifySpy = vi.spyOn(JSON, 'stringify')

    const { rerender } = render(<ToolResultPanelContent payload={payload} />)
    const parseCallsAfterFirstRender = parseSpy.mock.calls.length
    const stringifyCallsAfterFirstRender = stringifySpy.mock.calls.length

    rerender(<ToolResultPanelContent payload={payload} />)

    expect(parseSpy).toHaveBeenCalledTimes(parseCallsAfterFirstRender)
    expect(stringifySpy).toHaveBeenCalledTimes(stringifyCallsAfterFirstRender)
  })
})
