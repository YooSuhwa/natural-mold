'use client'

import { useMutation } from '@tanstack/react-query'
import { invitationsApi } from '@/lib/api/invitations'

/**
 * Accept an invitation for the signed-in account. The server also makes the joined
 * organization the current one (cookie), so the caller reloads into it (13.11 reset).
 */
export function useAcceptInvitation() {
  return useMutation({ mutationFn: (token: string) => invitationsApi.accept(token) })
}
