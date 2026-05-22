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

import { Badge } from '@/components/ui/badge'
import type {
  PublicationState,
  ResourcePublicationSummary,
} from '@/lib/types/marketplace'
import { cn } from '@/lib/utils'

interface Spec {
  label: string
  icon: LucideIcon
  className: string
}

const SPECS: Record<PublicationState, Spec> = {
  not_published: {
    label: 'Not published',
    icon: EyeOffIcon,
    className: 'bg-muted text-muted-foreground',
  },
  draft: {
    label: 'Draft',
    icon: FilePenIcon,
    className: 'bg-muted text-foreground',
  },
  published_private: {
    label: 'Private',
    icon: LockIcon,
    className: 'bg-muted text-foreground',
  },
  published_restricted: {
    label: 'Restricted',
    icon: UserCheckIcon,
    className: 'bg-status-accent/10 text-status-accent',
  },
  published_public_listed: {
    label: 'Listed',
    icon: CheckCircle2Icon,
    className: 'bg-status-success/10 text-status-success',
  },
  published_public_unlisted: {
    label: 'Unlisted (pending)',
    icon: HourglassIcon,
    className: 'bg-status-warn/10 text-status-warn',
  },
  published_unlisted: {
    label: 'Unlisted (link)',
    icon: LinkIcon,
    className: 'bg-status-info/10 text-status-info',
  },
  disabled: {
    label: 'Disabled',
    icon: BanIcon,
    className: 'bg-destructive/10 text-destructive',
  },
}

interface PublicationBadgeProps {
  summary?: ResourcePublicationSummary | null
  className?: string
}

export function PublicationBadge({ summary, className }: PublicationBadgeProps) {
  if (!summary) return null
  const spec = SPECS[summary.state] ?? SPECS.not_published
  const Icon = spec.icon
  return (
    <Badge className={cn('gap-1', spec.className, className)}>
      <Icon className="size-3" aria-hidden />
      <span>{spec.label}</span>
    </Badge>
  )
}
