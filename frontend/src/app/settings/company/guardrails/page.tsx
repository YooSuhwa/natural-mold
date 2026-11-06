import { CompanyGuardrailsContent } from '@/features/guardrails/components/company-guardrails-content'
import { SettingsShell } from '../../_components/settings-shell'

export default function CompanyGuardrailsPage() {
  return (
    <SettingsShell>
      <CompanyGuardrailsContent />
    </SettingsShell>
  )
}
