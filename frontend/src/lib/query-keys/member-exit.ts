import { organizationQueryKeys } from './organizations'

/** Under the org-admin subtree: org writes and org switches drop it with the rest. */
export const memberExitQueryKeys = {
  preview: (orgId: string, userId: string) =>
    [...organizationQueryKeys.orgAll(orgId), 'exit-preview', userId] as const,
}
