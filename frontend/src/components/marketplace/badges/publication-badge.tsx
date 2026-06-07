'use client'

import type { LucideIcon } from 'lucide-react'
import {
  BanIcon,
  CheckCircle2Icon,
  EyeOffIcon,
  FilePenIcon,
  HourglassIcon,
  LinkIcon,
  LockIcon,
  UserCheckIcon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Badge } from '@/components/ui/badge'
import type { PublicationState, ResourcePublicationSummary } from '@/lib/types/marketplace'
import { cn } from '@/lib/utils'

interface Spec {
  labelKey: string
  icon: LucideIcon
  className: string
}

const SPECS: Record<PublicationState, Spec> = {
  not_published: {
    labelKey: 'publication.not_published',
    icon: EyeOffIcon,
    className: 'bg-muted text-muted-foreground',
  },
  draft: {
    labelKey: 'publication.draft',
    icon: FilePenIcon,
    className: 'bg-muted text-foreground',
  },
  published_private: {
    labelKey: 'publication.published_private',
    icon: LockIcon,
    className: 'bg-muted text-foreground',
  },
  published_restricted: {
    labelKey: 'publication.published_restricted',
    icon: UserCheckIcon,
    className: 'bg-status-accent/10 text-status-accent',
  },
  published_public_listed: {
    labelKey: 'publication.published_public_listed',
    icon: CheckCircle2Icon,
    className: 'bg-status-success/10 text-status-success',
  },
  published_public_unlisted: {
    labelKey: 'publication.published_public_unlisted',
    icon: HourglassIcon,
    className: 'bg-status-warn/10 text-status-warn',
  },
  published_unlisted: {
    labelKey: 'publication.published_unlisted',
    icon: LinkIcon,
    className: 'bg-status-info/10 text-status-info',
  },
  disabled: {
    labelKey: 'publication.disabled',
    icon: BanIcon,
    className: 'bg-destructive/10 text-destructive',
  },
}

interface PublicationBadgeProps {
  summary?: ResourcePublicationSummary | null
  className?: string
}

export function PublicationBadge({ summary, className }: PublicationBadgeProps) {
  const t = useTranslations('marketplace.badges')
  if (!summary) return null
  const spec = SPECS[summary.state] ?? SPECS.not_published
  const Icon = spec.icon
  return (
    <Badge className={cn('gap-1', spec.className, className)}>
      <Icon className="size-3" aria-hidden />
      <span>{t(spec.labelKey)}</span>
    </Badge>
  )
}
