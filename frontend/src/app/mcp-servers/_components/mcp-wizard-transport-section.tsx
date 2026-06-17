import type { McpRegistryEntry, McpTransport } from '@/lib/types/mcp'

import type { McpWizardFormPatch, McpWizardFormState } from './mcp-wizard-form-state'
import { McpWizardManualSection } from './mcp-wizard-manual-section'
import { McpWizardRegistrySection } from './mcp-wizard-registry-section'

type McpWizardTransportSectionProps = {
  readonly registry: readonly McpRegistryEntry[]
  readonly state: McpWizardFormState
  readonly onChange: (patch: McpWizardFormPatch) => void
  readonly onPickRegistry: (entry: McpRegistryEntry) => void
  readonly onClearRegistry: () => void
  readonly onTransportChange: (transport: McpTransport) => void
  readonly onAddArg: () => void
}

export function McpWizardTransportSection({
  registry,
  state,
  onChange,
  onPickRegistry,
  onClearRegistry,
  onTransportChange,
  onAddArg,
}: McpWizardTransportSectionProps) {
  return (
    <div className="space-y-6">
      <McpWizardRegistrySection
        entries={registry}
        selected={state.registryKey}
        onSelect={onPickRegistry}
        onClear={onClearRegistry}
      />

      <McpWizardManualSection
        state={state}
        onChange={onChange}
        onTransportChange={onTransportChange}
        onAddArg={onAddArg}
      />
    </div>
  )
}
