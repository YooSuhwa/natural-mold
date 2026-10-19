/**
 * Module-level caches that hold org-scoped ids or content (pending submits,
 * loaded image URLs, ...) register a reset here; an organization switch runs
 * them all (plan 13.11).
 */
const resetters = new Set<() => void>()

export function registerOrgScopeReset(reset: () => void): void {
  resetters.add(reset)
}

export function resetOrgScopedModuleState(): void {
  for (const reset of resetters) reset()
}
