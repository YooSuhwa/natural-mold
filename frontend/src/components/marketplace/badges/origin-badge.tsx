'use client'

import type { LucideIcon } from 'lucide-react'
import {
  CogIcon,
  DownloadIcon,
  GlobeIcon,
  PencilIcon,
  SparklesIcon,
  UsersIcon,
} from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import type { OriginKind, ResourceOriginSummary } from '@/lib/types/marketplace'
import { cn } from '@/lib/utils'

interface Spec {
  label: string
  icon: LucideIcon
  className: string
}

const SPECS: Record<OriginKind, Spec> = {
  created_by_me: {
    label: 'Created by me',
    icon: PencilIcon,
    className: 'bg-muted text-foreground',
  },
  imported_by_me: {
    label: 'Imported by me',
    icon: DownloadIcon,
    className: 'bg-muted text-foreground',
  },
  built_in_k_skill: {
    label: 'Built-in · k-skill',
    icon: SparklesIcon,
    className: 'bg-primary/15 text-primary-strong',
  },
  shared_with_me: {
    label: 'Shared with me',
    icon: UsersIcon,
    className: 'bg-status-accent/10 text-status-accent',
  },
  community: {
    label: 'Community',
    icon: GlobeIcon,
    className: 'bg-status-info/10 text-status-info',
  },
  system_seed: {
    label: 'System',
    icon: CogIcon,
    className: 'bg-muted text-foreground',
  },
}

interface OriginBadgeProps {
  summary?: ResourceOriginSummary | null
  className?: string
}

export function OriginBadge({ summary, className }: OriginBadgeProps) {
  if (!summary) return null
  const spec = SPECS[summary.kind] ?? SPECS.created_by_me
  const label =
    summary.kind === 'shared_with_me' && summary.source_name
      ? `Shared by ${summary.source_name}`
      : (summary.label || spec.label)
  const Icon = spec.icon
  return (
    <Badge className={cn('gap-1', spec.className, className)}>
      <Icon className="size-3" aria-hidden />
      <span>{label}</span>
    </Badge>
  )
}
