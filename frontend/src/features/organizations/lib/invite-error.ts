import { ApiError } from '@/lib/api/errors'

/** S12 failure variants; anything else falls back to the shared `ApiErrorState`. */
export type InviteFailure = 'mismatch' | 'expired' | 'used' | 'invalid' | 'blocked' | 'unavailable'

const BY_CODE: Record<string, InviteFailure> = {
  invitation_email_mismatch: 'mismatch',
  tenant_invitation_email_mismatch: 'mismatch',
  invitation_expired: 'expired',
  tenant_invitation_expired: 'expired',
  invitation_used: 'used',
  tenant_invitation_used: 'used',
  // Revoked (or replaced by a resend) links read as unknown tokens on the server.
  invitation_invalid: 'invalid',
  tenant_invitation_invalid: 'invalid',
  tenant_invitation_revoked: 'invalid',
  invitation_membership_blocked: 'blocked',
  invitation_group_unavailable: 'unavailable',
  organization_unavailable: 'unavailable',
  tenant_unavailable: 'unavailable',
}

export function inviteFailure(error: unknown): InviteFailure | null {
  return error instanceof ApiError ? (BY_CODE[error.code] ?? null) : null
}
