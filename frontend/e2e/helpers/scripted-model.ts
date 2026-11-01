// Specs that create their own ``e2e_scripted`` models (a small context window for
// compaction, a 4096 window for the settings gauge) share one throwaway database
// with every later spec. Matching on the provider alone picks whichever scripted
// model the list returns first, so later specs silently ran against those
// fixtures. Match the seeded model by name instead (backend/app/seed/e2e_scripted_model.py).
export const SEEDED_SCRIPTED_MODEL_NAME = 'document-artifact-scripted'

export type SeededScriptedModel = { id: string; provider: 'e2e_scripted'; model_name: string }

export function isSeededScriptedModel(value: unknown): value is SeededScriptedModel {
  if (typeof value !== 'object' || value === null) return false
  const row = value as Record<string, unknown>
  return (
    row.provider === 'e2e_scripted' &&
    row.model_name === SEEDED_SCRIPTED_MODEL_NAME &&
    typeof row.id === 'string'
  )
}
