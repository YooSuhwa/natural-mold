import { CompanyMembersContent } from '@/features/company-settings/components/company-members-content'
import { SettingsShell } from '../../_components/settings-shell'

export default function CompanyMembersPage() {
  return (
    <SettingsShell>
      <CompanyMembersContent />
    </SettingsShell>
  )
}
