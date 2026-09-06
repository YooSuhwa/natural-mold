export const E2E_EXPORT_PROJECTS = Object.freeze([
  'scripted-smoke',
  'scripted-full',
  'scripted-capture',
  'live-manual',
])

const RESULT_ROOT_FILES = new Set([
  'junit.xml',
  'selection.json',
  'selection.log',
  'execution.json',
  'execution.log',
  'execution.stderr.log',
])
const ARTIFACT_LIKE = /\.(?:har|json|log|md|png|trace|webm|xml|zip)$/i
const SAFE_COMPONENT = /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$/
const PLAYWRIGHT_INTERNAL_RESULT_FILES = new Set([
  '.last-run.json',
  'playwright-artifacts/.last-run.json',
])

export function classifyArtifact(kind, relative, project) {
  if (kind === 'results' && PLAYWRIGHT_INTERNAL_RESULT_FILES.has(relative)) return undefined
  const components = relative.split('/')
  if (components.some((component) => !SAFE_COMPONENT.test(component))) {
    throw new Error('E2E artifact path is outside the explicit topology policy.')
  }
  const name = components.at(-1)
  const screenshot = name?.toLowerCase().endsWith('.png') ?? false
  let allowed = false
  if (kind === 'captures' || kind === 'legacy-captures') allowed = screenshot
  else if (components.length === 1) allowed = RESULT_ROOT_FILES.has(relative)
  else allowed = name === 'trace.zip' || name === 'error-context.md' || screenshot
  if (!allowed && ARTIFACT_LIKE.test(relative)) {
    throw new Error('Suspicious E2E artifact is outside the explicit allowlist.')
  }
  if (!allowed) return undefined
  if (screenshot && kind === 'results' && project !== 'scripted-capture') return undefined
  if (screenshot && project !== 'scripted-capture') {
    throw new Error('Screenshots may only be exported from the scripted-capture project.')
  }
  return { screenshot, trace: name === 'trace.zip' }
}

export function dateInSeoul(now) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(now)
  return Object.fromEntries(parts.map((part) => [part.type, part.value]))
}

export function validateSlug(slug) {
  const finalSlug =
    typeof slug === 'string' &&
    /^runtime-policy-final-[0-9a-f]{64}-(?:scripted|capture|live|f2-[0-9a-f]{16})$/.test(slug)
  if (
    typeof slug !== 'string' ||
    (!finalSlug && (!/^[a-z0-9]+(?:-[a-z0-9]+){0,9}$/.test(slug) || slug.length > 112))
  ) {
    throw new Error('E2E_EXPORT_SLUG must be a bounded lowercase-safe slug.')
  }
  return slug
}
