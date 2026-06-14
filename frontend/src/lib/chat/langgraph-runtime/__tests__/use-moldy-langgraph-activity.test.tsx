import { beforeEach, describe, expect, it } from 'vitest'
import type { ActivityProtocolEvent } from '../activity-protocol'
import { mocks, renderUsageHook, resetUsageMocks } from './use-moldy-langgraph-usage-test-harness'

const backgroundTaskValuesEvent = {
  type: 'event',
  method: 'values',
  run_id: 'run-bg',
  params: {
    namespace: [],
    data: {
      async_tasks: [{ id: 'bg-1', name: 'Background writer', status: 'running' }],
    },
  },
} satisfies ActivityProtocolEvent

describe('useMoldyLangGraphStream activity events', () => {
  beforeEach(resetUsageMocks)

  it('projects protocol values async tasks as background subagent activities', () => {
    mocks.useChannel.mockImplementation((_stream, channels: readonly string[]) =>
      channels.includes('values') ? [backgroundTaskValuesEvent] : [],
    )

    const { result } = renderUsageHook()

    expect(result.current.activities).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: 'run-bg:background_subagent:bg-1',
          kind: 'background_subagent',
          status: 'running',
          title: 'Background writer',
        }),
      ]),
    )
    expect(
      result.current.activities.some(
        (activity) =>
          activity.kind === 'subagent' &&
          (activity.id.includes('bg-1') ||
            activity.title === 'Background writer' ||
            activity.data?.id === 'bg-1'),
      ),
    ).toBe(false)
  })
})
