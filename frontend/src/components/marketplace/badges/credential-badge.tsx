'use client'

import type { LucideIcon } from 'lucide-react'
import { CircleDashedIcon, CloudIcon, KeyIcon, LogInIcon, PlusIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Badge } from '@/components/ui/badge'
import type { CredentialSummary, CredentialSummaryStatus } from '@/lib/types/marketplace'
import { cn } from '@/lib/utils'

interface Spec {
  labelKey: string
  icon: LucideIcon
  className: string
}

const SPECS: Record<CredentialSummaryStatus, Spec> = {
  none: {
    labelKey: 'credential.none',
    icon: CircleDashedIcon,
    className: 'bg-muted text-muted-foreground',
  },
  optional: {
    labelKey: 'credential.optional',
    icon: PlusIcon,
    className: 'bg-muted text-foreground',
  },
  required: {
    labelKey: 'credential.required',
    icon: KeyIcon,
    className: 'bg-status-warn/10 text-status-warn',
  },
  hosted_proxy: {
    labelKey: 'credential.hosted_proxy',
    icon: CloudIcon,
    className: 'bg-status-info/10 text-status-info',
  },
  manual_login: {
    labelKey: 'credential.manual_login',
    icon: LogInIcon,
    className: 'bg-status-accent/10 text-status-accent',
  },
}

interface CredentialBadgeProps {
  summary?: CredentialSummary | null
  className?: string
}

export function CredentialBadge({ summary, className }: CredentialBadgeProps) {
  const t = useTranslations('marketplace.badges')
  if (!summary) return null
  const spec = SPECS[summary.status] ?? SPECS.none
  const Icon = spec.icon
  const missing = summary.missing_required_count > 0
  return (
    <Badge
      className={cn('gap-1', spec.className, missing && 'ring-1 ring-destructive/40', className)}
    >
      <Icon className="size-3" aria-hidden />
      <span>{t(spec.labelKey)}</span>
      {missing ? (
        <span className="ml-1 inline-block size-1.5 rounded-full bg-destructive" aria-hidden />
      ) : null}
    </Badge>
  )
}
