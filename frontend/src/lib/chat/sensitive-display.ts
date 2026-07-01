const SAFE_TOKEN_METRIC_KEYS = new Set([
  'cache_creation_tokens',
  'cache_read_tokens',
  'completion_tokens',
  'estimated_cost',
  'input_token_details',
  'input_tokens',
  'output_token_details',
  'output_tokens',
  'prompt_tokens',
  'total_tokens',
  'usage',
  'usage_metadata',
])

const SENSITIVE_KEY_SOURCE =
  'password|api[_-]?key|secret|token|access[_-]?key|refresh[_-]?token|client[_-]?secret|private[_-]?key'
const SENSITIVE_KEY_PATTERN = new RegExp(`(?:${SENSITIVE_KEY_SOURCE})`, 'i')
const SENSITIVE_ASSIGNMENT_PATTERN = new RegExp(
  `((?:${SENSITIVE_KEY_SOURCE})["']?\\s*[:=]\\s*["']?)([^"',}\\]\\s]+)(["']?)`,
  'gi',
)
const BEARER_TOKEN_PATTERN = /\b(Bearer\s+)([A-Za-z0-9._~+/=-]+)/gi

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function isSensitiveDisplayKey(key: string): boolean {
  return !SAFE_TOKEN_METRIC_KEYS.has(key) && SENSITIVE_KEY_PATTERN.test(key)
}

export function redactSensitiveText(text: string): string {
  return text
    .replace(SENSITIVE_ASSIGNMENT_PATTERN, '$1<redacted>$3')
    .replace(BEARER_TOKEN_PATTERN, '$1<redacted>')
}

function redactSensitiveValue(value: unknown): unknown {
  if (typeof value === 'string') return redactSensitiveText(value)
  if (Array.isArray(value)) return value.map((item) => redactSensitiveValue(item))
  if (isRecord(value)) return redactSensitiveRecord(value)
  return value
}

export function redactSensitiveRecord(args: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(args).map(([key, value]) => [
      key,
      isSensitiveDisplayKey(key) ? '<redacted>' : redactSensitiveValue(value),
    ]),
  )
}
