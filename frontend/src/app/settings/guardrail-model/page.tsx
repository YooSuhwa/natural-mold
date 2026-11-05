import { SettingsShell } from '../_components/settings-shell'
import { GuardrailModelSettingsContent } from './_components/guardrail-model-settings-content'

export default function GuardrailModelSettingsPage() {
  return (
    <SettingsShell>
      <GuardrailModelSettingsContent />
    </SettingsShell>
  )
}
