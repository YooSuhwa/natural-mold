'use client'

import Link from 'next/link'
import type { LucideIcon } from 'lucide-react'
import { BotIcon, ServerIcon, SparklesIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { CredentialBadge } from '@/components/marketplace/badges/credential-badge'
import { InstallationBadge } from '@/components/marketplace/badges/installation-badge'
import { OriginBadge } from '@/components/marketplace/badges/origin-badge'
import { PublicationBadge } from '@/components/marketplace/badges/publication-badge'
import { SupportBadge } from '@/components/marketplace/badges/support-badge'
import type {
  MarketplaceItem,
  MarketplaceResourceType,
} from '@/lib/types/marketplace'
import { formatMediumDate } from '@/lib/utils/format-relative-time'
import { cn } from '@/lib/utils'

const RESOURCE_ICONS: Record<MarketplaceResourceType, LucideIcon> = {
  skill: SparklesIcon,
  agent: BotIcon,
  mcp: ServerIcon,
}

export type PrimaryCtaKind =
  | 'install'
  | 'open'
  | 'setup'
  | 'update'
  | 'review_update'
  | 'view_details'
  | 'disabled'

export interface PrimaryCta {
  kind: PrimaryCtaKind
  label: string
  variant: 'default' | 'outline'
  disabled?: boolean
}

export function derivePrimaryCta(item: MarketplaceItem): PrimaryCta {
  const installation = item.installation
  const supportLevel = item.execution_profile?.support_level

  if (item.status === 'disabled') {
    return { kind: 'disabled', label: 'Disabled', variant: 'outline', disabled: true }
  }

  if (
    (supportLevel === 'manual_only' || supportLevel === 'browser_or_local') &&
    !installation?.installed
  ) {
    return { kind: 'view_details', label: 'View details', variant: 'outline' }
  }

  if (!installation?.installed) {
    return { kind: 'install', label: 'Install', variant: 'default' }
  }

  if (installation.status === 'needs_setup') {
    return { kind: 'setup', label: 'Set up', variant: 'default' }
  }

  if (installation.update_available && installation.dirty) {
    return { kind: 'review_update', label: 'Review update', variant: 'outline' }
  }

  if (installation.update_available) {
    return { kind: 'update', label: 'Update', variant: 'default' }
  }

  if (installation.status === 'disabled') {
    return { kind: 'disabled', label: 'Disabled', variant: 'outline', disabled: true }
  }

  return { kind: 'open', label: 'Open', variant: 'outline' }
}

interface MarketplaceCardProps {
  item: MarketplaceItem
  onAction?: (item: MarketplaceItem, cta: PrimaryCta) => void
  className?: string
}

export function MarketplaceCard({ item, onAction, className }: MarketplaceCardProps) {
  const Icon = RESOURCE_ICONS[item.resource_type] ?? SparklesIcon
  const cta = derivePrimaryCta(item)
  const ownerLabel = item.is_system ? 'System' : 'Community'
  const versionLabel = item.latest_version?.version_label
  const versionDate = item.latest_version?.created_at

  return (
    <Card
      className={cn(
        'group/card relative flex h-full flex-col ring-1 ring-border/60 transition-all hover:ring-primary-strong/30',
        className,
      )}
    >
      <CardHeader className="flex-row items-start gap-3 pb-3">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-md bg-primary/15 text-primary-strong">
          <Icon className="size-5" aria-hidden />
        </div>
        <div className="min-w-0 flex-1">
          <Link
            href={`/marketplace/${item.id}`}
            className="text-sm font-semibold tracking-tight text-foreground hover:text-primary-strong"
          >
            {item.name}
          </Link>
          <p className="mt-0.5 truncate text-xs text-muted-foreground">
            {ownerLabel} · {item.resource_type}
          </p>
        </div>
        <div className="shrink-0">
          <Button
            variant={cta.variant}
            size="sm"
            disabled={cta.disabled}
            onClick={() => onAction?.(item, cta)}
            aria-label={cta.label}
          >
            {cta.label}
          </Button>
        </div>
      </CardHeader>

      <CardContent className="flex flex-1 flex-col gap-3 pt-0">
        {item.description ? (
          <p className="line-clamp-2 text-sm text-muted-foreground">
            {item.description}
          </p>
        ) : null}

        <div className="mt-auto flex flex-wrap items-center gap-1.5">
          <OriginBadge summary={item.origin_summary} />
          <PublicationBadge summary={item.publication_summary} />
          <CredentialBadge summary={item.credential_summary} />
          <SupportBadge profile={item.execution_profile} />
        </div>

        <div className="flex items-center justify-between text-[11px] text-muted-foreground">
          <InstallationBadge summary={item.installation} />
          {versionLabel ? (
            <span>
              v{versionLabel}
              {versionDate ? ` · ${formatMediumDate(versionDate)}` : null}
            </span>
          ) : null}
        </div>
      </CardContent>
    </Card>
  )
}
