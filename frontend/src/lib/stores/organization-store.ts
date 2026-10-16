import { atomWithStorage } from 'jotai/utils'

/**
 * Last organization this browser switched to. The authoritative selection is
 * the HttpOnly ``moldy_active_org`` cookie, which the client cannot read, so
 * this per-viewer hint only drives which org the switcher labels as current.
 */
export const activeOrganizationIdAtom = atomWithStorage<string | null>(
  'moldy.activeOrganizationId',
  null,
)
