import type { AccessEntry, AccessList } from '@/lib/types/grant'

export const grantsApiMock = {
  grantsApi: {
    list: vi.fn(),
    create: vi.fn(),
    access: vi.fn(),
    transfer: vi.fn(),
    revoke: vi.fn(),
  },
}

export const ME = 'user-me'

export function entry(
  overrides: Partial<AccessEntry> & Pick<AccessEntry, 'principal'>,
): AccessEntry {
  return {
    grant_id: null,
    level: 'viewer',
    source: 'direct',
    revocable: false,
    expires_at: null,
    ...overrides,
  }
}

export function accessList(overrides: Partial<AccessList> = {}): AccessList {
  return {
    items: [
      entry({
        principal: { type: 'user', id: ME, display_name: 'Me', member_count: null },
        level: 'owner',
        source: 'owner',
      }),
      entry({
        grant_id: 'grant-kim',
        principal: { type: 'user', id: 'user-kim', display_name: 'Kim', member_count: null },
        level: 'editor',
        revocable: true,
      }),
      entry({
        grant_id: 'grant-team',
        principal: { type: 'group', id: 'group-1', display_name: 'Team', member_count: 4 },
        level: 'consumer',
        source: 'group',
        revocable: true,
      }),
    ],
    my_permissions: ['can_share', 'can_revoke', 'can_govern'],
    ...overrides,
  }
}

/** Fail the test on any React / Base UI accessibility or prop warning. */
export function trapConsole() {
  const error = vi.spyOn(console, 'error').mockImplementation(() => undefined)
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
  return {
    assertClean() {
      expect(error.mock.calls).toEqual([])
      expect(warn.mock.calls).toEqual([])
    },
    restore() {
      error.mockRestore()
      warn.mockRestore()
    },
  }
}
