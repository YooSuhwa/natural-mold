import { OrgMembersContent } from '@/features/organizations/components/org-members-content'
import { SettingsShell } from '../../_components/settings-shell'

export default function OrganizationMembersPage() {
  return (
    <SettingsShell>
      <OrgMembersContent />
    </SettingsShell>
  )
}
