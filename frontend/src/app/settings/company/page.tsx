import { CompanyOrganizationsContent } from '@/features/organizations/components/company-organizations-content'
import { SettingsShell } from '../_components/settings-shell'

export default function CompanyOrganizationsPage() {
  return (
    <SettingsShell>
      <CompanyOrganizationsContent />
    </SettingsShell>
  )
}
