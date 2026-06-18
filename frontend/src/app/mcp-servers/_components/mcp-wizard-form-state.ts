import type {
  McpFromRegistryRequest,
  McpProbeRequest,
  McpRegistryEntry,
  McpServerCreateRequest,
  McpTransport,
} from '@/lib/types/mcp'

export type McpWizardTab = 'basics' | 'auth' | 'tools'

export type McpProbeState =
  | { readonly kind: 'idle' }
  | { readonly kind: 'pending' }
  | { readonly kind: 'ok'; readonly toolCount: number }
  | { readonly kind: 'error'; readonly message: string }

export type McpWizardKeyValueRow = {
  readonly key: string
  readonly value: string
}

export type McpWizardFormState = {
  readonly name: string
  readonly description: string
  readonly transport: McpTransport
  readonly url: string
  readonly command: string
  readonly args: readonly string[]
  readonly argDraft: string
  readonly envVars: readonly McpWizardKeyValueRow[]
  readonly headers: readonly McpWizardKeyValueRow[]
  readonly credentialId: string | null
  readonly registryKey: string | null
  readonly credentialDefinitionFilter: string | null
}

export type McpWizardFormPatch = Partial<McpWizardFormState>

export type McpOAuthCompletedMessage = {
  readonly type: 'moldy.oauth.completed'
  readonly credentialId?: string
}

export function createInitialMcpWizardState(): McpWizardFormState {
  return {
    name: '',
    description: '',
    transport: 'streamable_http',
    url: '',
    command: '',
    args: [],
    argDraft: '',
    envVars: [],
    headers: [],
    credentialId: null,
    registryKey: null,
    credentialDefinitionFilter: null,
  }
}

export function createMcpWizardStateFromRegistryEntry(entry: McpRegistryEntry): McpWizardFormState {
  return {
    ...createInitialMcpWizardState(),
    registryKey: entry.key,
    name: entry.display_name,
    description: entry.description ?? '',
    transport: entry.transport,
    url: entry.url ?? '',
    command: entry.command ?? '',
    args: entry.args ?? [],
    envVars: Object.entries(entry.env_vars ?? {}).map(([key, value]) => ({
      key,
      value: String(value),
    })),
    credentialDefinitionFilter: entry.credential_definition_key,
  }
}

export function clearMcpWizardRegistrySelection(state: McpWizardFormState): McpWizardFormState {
  return {
    ...state,
    registryKey: null,
    credentialDefinitionFilter: null,
  }
}

export function isMcpWizardBasicsValid(state: McpWizardFormState): boolean {
  if (!state.name.trim()) return false
  if (state.registryKey) return true
  if (state.transport === 'stdio') return state.command.trim().length > 0
  return state.url.trim().length > 0
}

export function keyValueRowsToRecord(
  rows: readonly McpWizardKeyValueRow[],
): Record<string, string> {
  const out: Record<string, string> = {}
  for (const { key, value } of rows) {
    const trimmedKey = key.trim()
    if (!trimmedKey) continue
    out[trimmedKey] = value
  }
  return out
}

export function buildMcpProbePayload(state: McpWizardFormState): McpProbeRequest {
  if (state.registryKey) {
    return { registry_key: state.registryKey, credential_id: state.credentialId }
  }
  return {
    transport: state.transport,
    url: state.transport === 'stdio' ? null : state.url.trim(),
    command: state.transport === 'stdio' ? state.command.trim() : null,
    headers: keyValueRowsToRecord(state.headers),
    credential_id: state.credentialId,
  }
}

export function buildMcpRegistryPayload(state: McpWizardFormState): McpFromRegistryRequest {
  return {
    registry_key: state.registryKey ?? '',
    name: state.name.trim(),
    credential_id: state.credentialId,
  }
}

export function buildMcpServerPayload(state: McpWizardFormState): McpServerCreateRequest {
  return {
    name: state.name.trim(),
    description: state.description.trim() || null,
    transport: state.transport,
    url: state.transport === 'stdio' ? null : state.url.trim(),
    command: state.transport === 'stdio' ? state.command.trim() : null,
    args: state.transport === 'stdio' ? [...state.args] : [],
    env_vars: state.transport === 'stdio' ? keyValueRowsToRecord(state.envVars) : {},
    headers: state.transport === 'stdio' ? {} : keyValueRowsToRecord(state.headers),
    credential_id: state.credentialId,
  }
}

export function splitMcpArgDraft(draft: string): readonly string[] {
  return draft
    .trim()
    .split(/[\s,]+/)
    .filter(Boolean)
}

export function appendMcpArgDraft(state: McpWizardFormState): McpWizardFormState {
  const parts = splitMcpArgDraft(state.argDraft)
  if (parts.length === 0) return state
  return {
    ...state,
    args: [...state.args, ...parts],
    argDraft: '',
  }
}

export function buildMcpOAuthInitialData(
  state: McpWizardFormState,
  selectedRegistryEntry: McpRegistryEntry | null,
): Record<string, string | boolean> {
  return {
    server_url: state.url.trim() || selectedRegistryEntry?.url || '',
    use_dynamic_client_registration: true,
    grant_type: 'pkce',
    authentication: 'none',
  }
}

export function buildMcpOAuthInitialName(
  state: McpWizardFormState,
  selectedRegistryEntry: McpRegistryEntry | null,
): string {
  const base = state.name.trim() || selectedRegistryEntry?.display_name || 'Atlassian Rovo'
  return `${base} OAuth`
}

export function countMcpToolParameters(schema: Record<string, unknown> | null | undefined): number {
  if (!schema) return 0
  const props = schema['properties']
  if (!props || typeof props !== 'object') return 0
  return Object.keys(props).length
}

export function isOAuthCompletedMessage(data: unknown): data is McpOAuthCompletedMessage {
  if (!data || typeof data !== 'object') return false
  if (!('type' in data) || data.type !== 'moldy.oauth.completed') return false
  if (!('credentialId' in data)) return true
  return typeof data.credentialId === 'string' || data.credentialId === undefined
}
