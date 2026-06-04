import { cn } from '@/lib/utils'

export type ResourceToneName = 'mint' | 'sky' | 'violet' | 'amber' | 'rose' | 'slate'
export type ResourceCardDensity = 'compact' | 'standard' | 'rich'

export type ResourceTone = {
  name: ResourceToneName
  card: string
  icon: string
  badge: string
  dot: string
}

const RESOURCE_TONES: Record<ResourceToneName, ResourceTone> = {
  mint: {
    name: 'mint',
    card: 'moldy-tone-card-mint',
    icon: 'moldy-tone-icon-mint',
    badge: 'moldy-tone-badge-mint',
    dot: 'bg-status-success',
  },
  sky: {
    name: 'sky',
    card: 'moldy-tone-card-sky',
    icon: 'moldy-tone-icon-sky',
    badge: 'moldy-tone-badge-sky',
    dot: 'bg-status-info',
  },
  violet: {
    name: 'violet',
    card: 'moldy-tone-card-violet',
    icon: 'moldy-tone-icon-violet',
    badge: 'moldy-tone-badge-violet',
    dot: 'bg-status-accent',
  },
  amber: {
    name: 'amber',
    card: 'moldy-tone-card-amber',
    icon: 'moldy-tone-icon-amber',
    badge: 'moldy-tone-badge-amber',
    dot: 'bg-status-warn',
  },
  rose: {
    name: 'rose',
    card: 'moldy-tone-card-rose',
    icon: 'moldy-tone-icon-rose',
    badge: 'moldy-tone-badge-rose',
    dot: 'bg-status-danger',
  },
  slate: {
    name: 'slate',
    card: 'moldy-tone-card-slate',
    icon: 'moldy-tone-icon-slate',
    badge: 'moldy-tone-badge-slate',
    dot: 'bg-muted-foreground',
  },
}

const CATEGORY_TONES: Record<string, ResourceToneName> = {
  account: 'amber',
  api: 'sky',
  automation: 'amber',
  communication: 'violet',
  general: 'mint',
  http: 'sky',
  llm: 'mint',
  mcp: 'sky',
  oauth: 'violet',
  package: 'violet',
  productivity: 'amber',
  search: 'sky',
  skill: 'violet',
  stdio: 'sky',
  streamable_http: 'sky',
  system: 'slate',
  text: 'mint',
}

const STATUS_TONES: Record<string, ResourceToneName> = {
  active: 'mint',
  connected: 'mint',
  healthy: 'mint',
  normal: 'mint',
  auth_needed: 'amber',
  degraded: 'amber',
  expired: 'amber',
  paused: 'amber',
  disabled: 'slate',
  inactive: 'slate',
  unknown: 'slate',
  completed: 'slate',
  error: 'rose',
  failed: 'rose',
  unreachable: 'rose',
  unhealthy: 'rose',
}

export const resourceMetaClassName = 'moldy-resource-meta'
export const resourceStatusChipClassName = 'moldy-resource-status-chip'

export function getResourceTone(name: string | null | undefined): ResourceTone {
  const key = normalizeKey(name)
  return RESOURCE_TONES[CATEGORY_TONES[key] ?? 'mint']
}

export function getStatusResourceTone(status: string | null | undefined): ResourceTone {
  const key = normalizeKey(status)
  return RESOURCE_TONES[STATUS_TONES[key] ?? 'slate']
}

export function resourceCardClassName(
  tone: ResourceTone,
  className?: string,
  options: { density?: ResourceCardDensity; interactive?: boolean } = {},
): string {
  const interactive = options.interactive ?? true
  return cn(
    'moldy-resource-card group',
    interactive && 'moldy-resource-card-interactive',
    options.density && `moldy-resource-card-${options.density}`,
    tone.card,
    className,
  )
}

function normalizeKey(value: string | null | undefined): string {
  return (value ?? '').trim().toLowerCase().replaceAll('-', '_')
}
