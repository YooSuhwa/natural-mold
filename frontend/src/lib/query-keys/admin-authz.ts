/** `['admin-authz', ...]`: platform scope, independent of the active organization. */
export const adminAuthzQueryKeys = {
  all: ['admin-authz'] as const,
  status: () => ['admin-authz', 'status'] as const,
  failures: () => ['admin-authz', 'failures'] as const,
}
