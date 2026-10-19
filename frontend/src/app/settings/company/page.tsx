import { CompanyProfileContent } from '@/features/company-settings/components/company-profile-card'
import { CompanyOrganizationsContent } from '@/features/organizations/components/company-organizations-content'
import { SettingsShell } from '../_components/settings-shell'

export default function CompanyOrganizationsPage() {
  return (
    <SettingsShell>
      <div className="space-y-8">
        <CompanyProfileContent />
        <CompanyOrganizationsContent />
      </div>
    </SettingsShell>
  )
}
