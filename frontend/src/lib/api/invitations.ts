import { apiFetch } from './client'

/** `POST /api/invitations/accept` (B-05): org (`ov1.`) or company (`tv1.`) invitations. */
export interface InvitationAccepted {
  org_id: string | null
  tenant_id: string
  user_id: string
}

export const invitationsApi = {
  accept: (token: string) =>
    apiFetch<InvitationAccepted>('/api/invitations/accept', {
      method: 'POST',
      body: JSON.stringify({ token }),
    }),
}
