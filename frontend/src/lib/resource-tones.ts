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
    icon:
      'bg-white/70 text-emerald-700 ring-emerald-200/70 dark:bg-white/10 dark:text-emerald-200 dark:ring-emerald-400/20',
    badge:
      'border-emerald-200/70 bg-white/70 text-emerald-800 dark:border-emerald-400/20 dark:bg-white/10 dark:text-emerald-200',
    dot: 'bg-status-success',
  },
  sky: {
    name: 'sky',
    card: 'bg-[var(--moldy-sky)] hover:border-[var(--moldy-border-sky)]',
    icon:
      'bg-white/70 text-sky-700 ring-sky-200/70 dark:bg-white/10 dark:text-sky-200 dark:ring-sky-400/20',
    badge:
      'border-sky-200/70 bg-white/70 text-sky-800 dark:border-sky-400/20 dark:bg-white/10 dark:text-sky-200',
    dot: 'bg-status-info',
  },
  violet: {
    name: 'violet',
    card: 'bg-[var(--moldy-violet)] hover:border-[var(--moldy-border-violet)]',
    icon:
      'bg-white/70 text-violet-700 ring-violet-200/70 dark:bg-white/10 dark:text-violet-200 dark:ring-violet-400/20',
    badge:
      'border-violet-200/70 bg-white/70 text-violet-800 dark:border-violet-400/20 dark:bg-white/10 dark:text-violet-200',
    dot: 'bg-status-accent',
  },
  amber: {
    name: 'amber',
    card: 'bg-[var(--moldy-amber)] hover:border-[var(--moldy-border-amber)]',
    icon:
      'bg-white/70 text-amber-700 ring-amber-200/70 dark:bg-white/10 dark:text-amber-200 dark:ring-amber-400/20',
    badge:
      'border-amber-200/70 bg-white/70 text-amber-800 dark:border-amber-400/20 dark:bg-white/10 dark:text-amber-200',
    dot: 'bg-status-warn',
  },
  rose: {
    name: 'rose',
    card: 'bg-[var(--moldy-rose)] hover:border-[var(--moldy-border-rose)]',
    icon:
      'bg-white/70 text-rose-700 ring-rose-200/70 dark:bg-white/10 dark:text-rose-200 dark:ring-rose-400/20',
    badge:
      'border-rose-200/70 bg-white/70 text-rose-800 dark:border-rose-400/20 dark:bg-white/10 dark:text-rose-200',
    dot: 'bg-status-danger',
  },
  slate: {
    name: 'slate',
    card: 'bg-[var(--moldy-slate)] hover:border-[var(--moldy-border-slate)]',
    icon:
      'bg-white/70 text-slate-600 ring-slate-200/70 dark:bg-white/10 dark:text-slate-200 dark:ring-slate-400/20',
    badge:
      'border-slate-200/70 bg-white/70 text-slate-700 dark:border-slate-400/20 dark:bg-white/10 dark:text-slate-200',
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
