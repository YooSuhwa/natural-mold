import { SettingsShell } from '../../_components/settings-shell'
import { PlatformOperatorGate } from '@/features/platform-tenants/components/platform-operator-gate'
import { TenantListContent } from '@/features/platform-tenants/components/tenant-list-content'

/** S03 플랫폼 운영: 회사 목록 (plan 13.14, 13.16; U-01). Server wrapper, client islands. */
export default function PlatformTenantsPage() {
  return (
    <SettingsShell wide>
      <PlatformOperatorGate>
        <TenantListContent />
      </PlatformOperatorGate>
    </SettingsShell>
  )
}
