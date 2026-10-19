import { apiFetch } from './client'
import { organizationVersionHeader } from './organizations'
import type { MemberExitPreview, MemberRemoval, MemberRemovalResult } from '@/lib/types/member-exit'

function memberPath(orgId: string, userId: string): string {
  return `/api/orgs/${encodeURIComponent(orgId)}/members/${encodeURIComponent(userId)}`
}

/** E-02 removal wizard (S15): read-only preview, then the settled removal. */
export const memberExitApi = {
  preview: (orgId: string, userId: string) =>
    apiFetch<MemberExitPreview>(`${memberPath(orgId, userId)}/exit-preview`),
  remove: (orgId: string, userId: string, data: MemberRemoval, version: string) =>
    apiFetch<MemberRemovalResult>(`${memberPath(orgId, userId)}/removal`, {
      method: 'POST',
      headers: organizationVersionHeader(version),
      body: JSON.stringify(data),
    }),
}
