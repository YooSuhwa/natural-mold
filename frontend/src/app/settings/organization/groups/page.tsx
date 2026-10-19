import { OrgGroupsContent } from '@/features/organizations/components/org-groups-content'
import { SettingsShell } from '../../_components/settings-shell'

export default function OrganizationGroupsPage() {
  return (
    <SettingsShell>
      <OrgGroupsContent />
    </SettingsShell>
  )
}
