import { OrgCatalogContent } from '@/features/organizations/components/org-catalog-content'
import { SettingsShell } from '../../_components/settings-shell'

export default function OrganizationCatalogPage() {
  return (
    <SettingsShell>
      <OrgCatalogContent />
    </SettingsShell>
  )
}
