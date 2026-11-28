import type { Model } from '@/lib/types/model'
import type { OrgImageDescriptionPolicy } from '@/lib/types/organization'

type Destination = Pick<OrgImageDescriptionPolicy, 'model_id' | 'provider' | 'base_url'>

const defaultBases = {
  openai: 'https://api.openai.com/v1',
  openrouter: 'https://openrouter.ai/api/v1',
  openai_compatible: '',
}

/** Only offer a model that can bind a credential, a price ceiling and one HTTPS destination. */
export function imagePolicyDestination(model: Model): Destination | null {
  if (
    model.kind !== 'chat' ||
    !model.is_visible ||
    !model.supports_vision ||
    !model.default_credential_id ||
    !model.context_window ||
    !Number.isSafeInteger(model.context_window) ||
    model.context_window < 1 ||
    model.cost_per_input_token == null ||
    model.cost_per_input_token < 0 ||
    model.cost_per_output_token == null ||
    model.cost_per_output_token < 0
  )
    return null
  const provider = model.provider
  if (provider !== 'openai' && provider !== 'openai_compatible' && provider !== 'openrouter')
    return null
  const base = model.base_url || defaultBases[provider]
  if (
    [...base].some((char) => char <= ' ') ||
    !/^https:\/\//iu.test(base) ||
    /[\\?#\s]/u.test(base) ||
    base.length > 500
  )
    return null
  try {
    const url = new URL(base)
    if (url.protocol !== 'https:' || url.username || url.password) return null
    const base_url = url.href.replace(/\/+$/u, '')
    if (provider === 'openai' && base_url !== defaultBases.openai) return null
    return { model_id: model.id, provider, base_url }
  } catch {
    return null
  }
}
