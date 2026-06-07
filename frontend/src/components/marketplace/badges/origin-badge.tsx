'use client'

import type { LucideIcon } from 'lucide-react'
import { CogIcon, DownloadIcon, GlobeIcon, PencilIcon, SparklesIcon, UsersIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Badge } from '@/components/ui/badge'
import type { OriginKind, ResourceOriginSummary } from '@/lib/types/marketplace'
import { cn } from '@/lib/utils'

interface Spec {
  labelKey: string
  icon: LucideIcon
  className: string
}

const SPECS: Record<OriginKind, Spec> = {
  created_by_me: {
    labelKey: 'origin.created_by_me',
    icon: PencilIcon,
    className: 'bg-muted text-foreground',
  },
  imported_by_me: {
    labelKey: 'origin.imported_by_me',
    icon: DownloadIcon,
    className: 'bg-muted text-foreground',
  },
  built_in_k_skill: {
    labelKey: 'origin.built_in_k_skill',
    icon: SparklesIcon,
    className: 'bg-primary/15 text-primary-strong',
  },
  shared_with_me: {
    labelKey: 'origin.shared_with_me',
    icon: UsersIcon,
    className: 'bg-status-accent/10 text-status-accent',
  },
  community: {
    labelKey: 'origin.community',
    icon: GlobeIcon,
    className: 'bg-status-info/10 text-status-info',
  },
  system_seed: {
    labelKey: 'origin.system_seed',
    icon: CogIcon,
    className: 'bg-muted text-foreground',
  },
}

interface OriginBadgeProps {
  summary?: ResourceOriginSummary | null
  className?: string
}

export function OriginBadge({ summary, className }: OriginBadgeProps) {
  const t = useTranslations('marketplace.badges')
  if (!summary) return null
  const spec = SPECS[summary.kind] ?? SPECS.created_by_me
  const label =
    summary.kind === 'shared_with_me' && summary.source_name
      ? t('origin.sharedBy', { name: summary.source_name })
      : summary.label || t(spec.labelKey)
  const Icon = spec.icon
  return (
    <Badge className={cn('gap-1', spec.className, className)}>
      <Icon className="size-3" aria-hidden />
      <span>{label}</span>
    </Badge>
  )
}
