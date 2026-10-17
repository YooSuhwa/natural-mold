/**
 * SaaS operator company administration (plan 9.7, S03/S04; B-12, I-07, U-01).
 * Metadata only: the operator never sees a company's agents or conversations.
 */

export type PlatformTenantStatus = 'active' | 'suspended' | 'deletion_scheduled'

/** Backend `TenantLimitSummary`; decimals arrive as strings. `null` = no limit set. */
export interface PlatformTenantLimits {
  member_limit: number | null
  monthly_spend_limit: string | number | null
  concurrent_run_limit: number | null
  allow_system_models: boolean
}

/** Backend `AdminTenantDetail`. */
export interface PlatformTenant {
  id: string
  name: string
  slug: string
  status: PlatformTenantStatus
  created_at: string
  updated_at: string
  limits: PlatformTenantLimits
  organization_count: number
  active_member_count: number
}

export interface PlatformTenantPage {
  items: PlatformTenant[]
  next_cursor: string | null
  has_more: boolean
}

export interface PlatformTenantQuery {
  status?: PlatformTenantStatus | null
  search?: string | null
  cursor?: string | null
}

export interface PlatformTenantCreate {
  name: string
  slug: string
  first_admin_email: string
  first_organization_name: string
  member_limit: number | null
  monthly_spend_limit: number | null
  concurrent_run_limit: number | null
  allow_system_models: boolean
}

/** Backend `PlatformTenantMetadataPatch`: only the fields sent are changed. */
export interface PlatformTenantPatch {
  name?: string
  member_limit?: number | null
  monthly_spend_limit?: number | null
  concurrent_run_limit?: number | null
  allow_system_models?: boolean
}

/** Backend `TenantInvitationCreated`. `token` is a one-time secret: never log or persist it. */
export interface PlatformTenantInvitation {
  id: string
  email: string
  role: string | null
  expires_at: string
  created_at: string
  status: string
  token: string | null
}

export interface PlatformTenantCreateResult {
  tenant: PlatformTenant
  organization: { id: string; name: string }
  invitation: PlatformTenantInvitation
}

/** `GET /api/admin/tenants/{id}/limits` (backend `UsageLimitView`, company scope). */
export interface PlatformTenantUsage {
  used: string | number
  monthly_spend_limit: string | number | null
  resets_at: string
  member_limit: number | null
  members: number | null
  concurrent_run_limit: number | null
  active_runs: number | null
}

export interface PlatformTenantDeletion {
  tenant: PlatformTenant
  deletion_due_at: string | null
}

export type PlatformTenantExportStatus = 'queued' | 'running' | 'succeeded' | 'failed'

export interface PlatformTenantExport {
  id: string
  tenant_id: string
  status: PlatformTenantExportStatus
  progress: number
  error_code: string | null
  size_bytes: number | null
  created_at: string
  started_at: string | null
  completed_at: string | null
  expires_at: string | null
  download_available: boolean
}

export interface PlatformInstallation {
  deployment_mode: 'single_tenant' | 'multi_tenant'
  org_multi_enabled: boolean
  platform_tenant_management_enabled: boolean
}
