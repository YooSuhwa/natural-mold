import { OrgAccessRequestsContent } from '@/features/organizations/components/org-access-requests-content'
import { SettingsShell } from '../../_components/settings-shell'

export default function OrganizationAccessRequestsPage() {
  return (
    <SettingsShell>
      <OrgAccessRequestsContent />
    </SettingsShell>
  )
}
