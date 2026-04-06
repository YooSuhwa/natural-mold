export function getProviderIcon(providerType: string): string {
  switch (providerType) {
    case 'openai':
      return 'OAI'
    case 'anthropic':
      return 'ANT'
    case 'google':
      return 'GGL'
    case 'openrouter':
      return 'ORT'
    case 'openai_compatible':
      return 'LCL'
    default:
      return 'AI'
  }
}

export function getProviderLabel(providerType: string): string {
  switch (providerType) {
    case 'openai':
      return 'OpenAI'
    case 'anthropic':
      return 'Anthropic'
    case 'google':
      return 'Google (Gemini)'
    case 'openrouter':
      return 'OpenRouter'
    case 'openai_compatible':
      return 'OpenAI Compatible'
    default:
      return providerType
  }
}

export function formatContextWindow(tokens: number | null | undefined): string | null {
  if (!tokens) return null
  if (tokens >= 1000000) return `${(tokens / 1000000).toFixed(0)}M`
  if (tokens >= 1000) return `${(tokens / 1000).toFixed(0)}K`
  return `${tokens}`
}
