import { cn } from '@/lib/utils'

export type ResourceToneName = 'mint' | 'sky' | 'violet' | 'amber' | 'rose' | 'slate'

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
    card: 'bg-[var(--moldy-mint)] hover:border-[var(--moldy-border-mint)]',
    icon: 'moldy-tone-icon-mint',
    badge: 'moldy-tone-badge-mint',
    dot: 'bg-status-success',
  },
  sky: {
    name: 'sky',
    card: 'bg-[var(--moldy-sky)] hover:border-[var(--moldy-border-sky)]',
    icon: 'moldy-tone-icon-sky',
    badge: 'moldy-tone-badge-sky',
    dot: 'bg-status-info',
  },
  violet: {
    name: 'violet',
    card: 'bg-[var(--moldy-violet)] hover:border-[var(--moldy-border-violet)]',
    icon: 'moldy-tone-icon-violet',
    badge: 'moldy-tone-badge-violet',
    dot: 'bg-status-accent',
  },
  amber: {
    name: 'amber',
    card: 'bg-[var(--moldy-amber)] hover:border-[var(--moldy-border-amber)]',
    icon: 'moldy-tone-icon-amber',
    badge: 'moldy-tone-badge-amber',
    dot: 'bg-status-warn',
  },
  rose: {
    name: 'rose',
    card: 'bg-[var(--moldy-rose)] hover:border-[var(--moldy-border-rose)]',
    icon: 'moldy-tone-icon-rose',
    badge: 'moldy-tone-badge-rose',
    dot: 'bg-status-danger',
  },
  slate: {
    name: 'slate',
    card: 'bg-[var(--moldy-slate)] hover:border-[var(--moldy-border-slate)]',
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

export function resourceCardClassName(tone: ResourceTone, className?: string): string {
  return cn(
    'moldy-resource-card group',
    tone.card,
    className,
  )
}

function normalizeKey(value: string | null | undefined): string {
  return (value ?? '').trim().toLowerCase().replaceAll('-', '_')
}
