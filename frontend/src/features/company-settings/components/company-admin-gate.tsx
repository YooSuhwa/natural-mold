'use client'

import type { ReactNode } from 'react'
import { useTranslations } from 'next-intl'

import { ApiErrorState } from '@/components/shared/api-error-state'
import { EmptyState } from '@/components/shared/empty-state'
import { Skeleton } from '@/components/ui/skeleton'
import { OrgPageFrame } from '@/features/organizations/components/org-page-frame'
import { useTenantOverview } from '@/lib/hooks/use-organizations'
import type { TenantOverviewDetail } from '@/lib/types/tenant-admin'

export interface CompanyScope {
  tenantId: string
  overview: TenantOverviewDetail
  /** Company admin (`can_manage_tenant` + `can_manage_tenant_catalog`). */
  isAdmin: boolean
}

interface CompanyAdminGateProps {
  title: string
  description: string
  /** Admin-only screens (S06, S07) show nothing else to members and auditors. */
  requireAdmin?: boolean
  children: (scope: CompanyScope) => ReactNode
}

/**
 * Company settings frame (S05–S07). Fails closed: nothing renders until the caller's
 * company authority (`GET /api/tenants/{id}` `access`) has loaded, and a load error
 * shows the shared error state instead of controls. The server still enforces every call.
 */
export function CompanyAdminGate({
  title,
  description,
  requireAdmin = false,
  children,
}: CompanyAdminGateProps) {
  return (
    <OrgPageFrame title={title} description={description}>
      {(organization) => (
        <GateBody tenantId={organization.tenant_id} requireAdmin={requireAdmin}>
          {children}
        </GateBody>
      )}
    </OrgPageFrame>
  )
}

function GateBody({
  tenantId,
  requireAdmin,
  children,
}: {
  tenantId: string
  requireAdmin: boolean
  children: (scope: CompanyScope) => ReactNode
}) {
  const t = useTranslations('orgAdmin.companySettings')
  const overview = useTenantOverview(tenantId)

  if (overview.isLoading) return <Skeleton className="h-32 w-full" data-testid="company-loading" />
  if (!overview.data) {
    return <ApiErrorState error={overview.error} onRetry={() => void overview.refetch()} />
  }
  const isAdmin = overview.data.access === 'admin'
  if (requireAdmin && !isAdmin) {
    return <EmptyState title={t('adminOnly')} description={t('adminOnlyDescription')} />
  }
  return <>{children({ tenantId, overview: overview.data, isAdmin })}</>
}
