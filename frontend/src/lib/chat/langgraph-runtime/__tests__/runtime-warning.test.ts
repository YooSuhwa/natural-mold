import { beforeEach, describe, expect, it, vi } from 'vitest'
import { reportRuntimeFailure } from '../runtime-warning'

const mocks = vi.hoisted(() => ({ reportClientWarning: vi.fn() }))

vi.mock('@/lib/logging/client-logger', () => ({
  reportClientWarning: mocks.reportClientWarning,
}))

describe('reportRuntimeFailure', () => {
  beforeEach(() => mocks.reportClientWarning.mockClear())

  it('does not forward raw SDK failure details to the client logger', () => {
    const sentinel = 'sk-secret-key-in-header-and-body'

    reportRuntimeFailure(new Error(sentinel), 'initial_hydration_failed')

    expect(mocks.reportClientWarning).toHaveBeenCalledWith(
      'useMoldyLangGraphStream',
      'Runtime operation failed: initial_hydration_failed',
    )
    expect(JSON.stringify(mocks.reportClientWarning.mock.calls)).not.toContain(sentinel)
  })
})
