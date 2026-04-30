/**
 * Single source of truth for "which credential should this model use" picks
 * on the frontend. Mirrors the backend's
 * ``app.services.credential_resolver.resolve_credential_for_model`` and lives
 * here so every UI surface (models list row [Check], single-row [Test]
 * dialog, model detail Health panel) returns identical results.
 *
 * Tiered policy:
 *   1) ``model.default_credential_id`` — captured at Add-model time, the
 *      user's explicit intent. Wins as long as the credential still exists.
 *   2) Provider definition_key match — surfaces a sensible default when no
 *      explicit binding exists.
 *   3) First available LLM credential — last resort so a freshly-set-up
 *      account still has a valid pick.
 */

import type { Credential } from '@/lib/types/credential'
import type { Model } from '@/lib/types/model'

/**
 * Credential definition keys that supply LLM API access. Used by the picker
 * to filter out non-LLM rows (Naver search, Google search, MCP OAuth, ...).
 * Kept in one place so a new provider only needs touching here.
 */
export const LLM_DEFINITION_KEYS = [
  'openai',
  'anthropic',
  'google_genai',
  'azure_openai',
  'openrouter',
  'openai_compatible',
] as const

export function filterLlmCredentials(
  credentials: readonly Credential[] | null | undefined,
): Credential[] {
  if (!credentials) return []
  const llm = new Set<string>(LLM_DEFINITION_KEYS)
  return credentials.filter((c) => llm.has(c.definition_key))
}

/**
 * Pick the credential to use for a model. Returns ``undefined`` only when
 * the user has zero LLM credentials.
 */
export function resolveCredentialForModel(
  model: Pick<Model, 'default_credential_id' | 'provider'>,
  llmCredentials: readonly Credential[],
): string | undefined {
  if (model.default_credential_id) {
    const stored = llmCredentials.find((c) => c.id === model.default_credential_id)
    if (stored) return stored.id
  }
  const exact = llmCredentials.find((c) => c.definition_key === model.provider)
  return (exact ?? llmCredentials[0])?.id
}
