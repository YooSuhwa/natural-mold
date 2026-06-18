import { describe, expect, it } from 'vitest'

import {
  appendMcpArgDraft,
  buildMcpProbePayload,
  buildMcpRegistryPayload,
  buildMcpServerPayload,
  createInitialMcpWizardState,
  createMcpWizardStateFromRegistryEntry,
  isMcpWizardBasicsValid,
  isOAuthCompletedMessage,
  keyValueRowsToRecord,
  splitMcpArgDraft,
} from '../mcp-wizard-form-state'
import type { McpRegistryEntry } from '@/lib/types/mcp'

describe('mcp wizard form state', () => {
  it('builds streamable http payload with env and headers', () => {
    const state = createInitialMcpWizardState()
    const payload = buildMcpServerPayload({
      ...state,
      name: 'Docs',
      transport: 'streamable_http',
      url: 'https://example.com/mcp',
      headers: [{ key: 'X-Test', value: 'ok' }],
    })

    expect(payload).toMatchObject({
      name: 'Docs',
      transport: 'streamable_http',
      url: 'https://example.com/mcp',
      headers: { 'X-Test': 'ok' },
    })
    expect(payload.env_vars).toEqual({})
    expect(payload.args).toEqual([])
  })

  it('builds stdio payload with command args and env vars', () => {
    const state = createInitialMcpWizardState()
    const payload = buildMcpServerPayload({
      ...state,
      name: 'GitHub',
      transport: 'stdio',
      command: 'npx',
      args: ['-y', '@modelcontextprotocol/server-github'],
      envVars: [
        { key: ' GITHUB_TOKEN ', value: '{{$credentials.token}}' },
        { key: '', value: 'ignored' },
      ],
      headers: [{ key: 'Authorization', value: 'Bearer ignored' }],
    })

    expect(payload).toMatchObject({
      name: 'GitHub',
      transport: 'stdio',
      command: 'npx',
      url: null,
      args: ['-y', '@modelcontextprotocol/server-github'],
      env_vars: { GITHUB_TOKEN: '{{$credentials.token}}' },
      headers: {},
    })
  })

  it('maps registry entries into a complete form state and registry payload', () => {
    const entry: McpRegistryEntry = {
      key: 'atlassian-rovo',
      display_name: 'Atlassian Rovo',
      description: 'Rovo tools',
      icon_id: 'server',
      transport: 'streamable_http',
      url: 'https://example.com/mcp',
      command: null,
      args: null,
      env_vars: { ROVO_TOKEN: 'secret' },
      credential_definition_key: 'mcp_oauth2',
      documentation_url: null,
    }

    const state = createMcpWizardStateFromRegistryEntry(entry)

    expect(state).toMatchObject({
      registryKey: 'atlassian-rovo',
      name: 'Atlassian Rovo',
      credentialDefinitionFilter: 'mcp_oauth2',
      envVars: [{ key: 'ROVO_TOKEN', value: 'secret' }],
    })
    expect(buildMcpProbePayload({ ...state, credentialId: 'cred-1' })).toEqual({
      registry_key: 'atlassian-rovo',
      credential_id: 'cred-1',
    })
    expect(buildMcpRegistryPayload({ ...state, credentialId: 'cred-1' })).toEqual({
      registry_key: 'atlassian-rovo',
      name: 'Atlassian Rovo',
      credential_id: 'cred-1',
    })
  })

  it('validates basics by transport-specific required fields', () => {
    const state = createInitialMcpWizardState()

    expect(isMcpWizardBasicsValid(state)).toBe(false)
    expect(isMcpWizardBasicsValid({ ...state, name: 'HTTP', url: 'https://example.com' })).toBe(
      true,
    )
    expect(isMcpWizardBasicsValid({ ...state, name: 'stdio', transport: 'stdio' })).toBe(false)
    expect(
      isMcpWizardBasicsValid({ ...state, name: 'stdio', transport: 'stdio', command: 'npx' }),
    ).toBe(true)
    expect(isMcpWizardBasicsValid({ ...state, name: 'Preset', registryKey: 'rovo' })).toBe(true)
  })

  it('splits arg drafts and appends them to state', () => {
    const state = createInitialMcpWizardState()

    expect(splitMcpArgDraft(' -y, pkg --stdio ')).toEqual(['-y', 'pkg', '--stdio'])
    expect(appendMcpArgDraft({ ...state, argDraft: '-y, pkg' })).toMatchObject({
      args: ['-y', 'pkg'],
      argDraft: '',
    })
    expect(appendMcpArgDraft(state)).toBe(state)
  })

  it('keeps only named key value rows', () => {
    expect(
      keyValueRowsToRecord([
        { key: ' Authorization ', value: 'Bearer token' },
        { key: '', value: 'ignored' },
      ]),
    ).toEqual({ Authorization: 'Bearer token' })
  })

  it('narrows oauth completion messages without accepting malformed payloads', () => {
    expect(isOAuthCompletedMessage({ type: 'moldy.oauth.completed', credentialId: 'cred-1' })).toBe(
      true,
    )
    expect(isOAuthCompletedMessage({ type: 'moldy.oauth.completed', credentialId: 1 })).toBe(false)
    expect(isOAuthCompletedMessage({ type: 'other' })).toBe(false)
  })
})
