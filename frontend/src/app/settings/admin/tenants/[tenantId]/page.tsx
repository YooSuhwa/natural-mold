import { SettingsShell } from '../../../_components/settings-shell'
import { PlatformOperatorGate } from '@/features/platform-tenants/components/platform-operator-gate'
import { TenantDetailContent } from '@/features/platform-tenants/components/tenant-detail-content'

/** S04 회사 상세 (plan 13.14, 13.16; U-01). Server wrapper, client islands. */
export default async function PlatformTenantDetailPage({
  params,
}: {
  params: Promise<{ tenantId: string }>
}) {
  const { tenantId } = await params
  return (
    <SettingsShell>
      <PlatformOperatorGate>
        <TenantDetailContent tenantId={tenantId} />
      </PlatformOperatorGate>
    </SettingsShell>
  )
}
