/** Opt-in control for the org-authz specs (skip-manifest condition E2E_ORG_AUTHZ). */
export const ORG_AUTHZ_OPT_IN = 'E2E_ORG_AUTHZ'

export function validateOrgAuthzOptIn(lane, project, environment) {
  const value = environment[ORG_AUTHZ_OPT_IN]
  if (typeof value !== 'string' || value.trim().length === 0) return
  if (value !== '1') throw new Error('E2E_ORG_AUTHZ=1 is the only supported org-authz opt-in.')
  if (lane !== 'scripted' || project !== 'scripted-full') {
    throw new Error('E2E_ORG_AUTHZ is only valid for scripted-full.')
  }
}
