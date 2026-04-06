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
