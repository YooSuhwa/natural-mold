'use client'

import Link from 'next/link'
import { memo } from 'react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { DomainIcon, getDomainIconIdForResource } from '@/components/shared/icon'
import { CredentialBadge } from '@/components/marketplace/badges/credential-badge'
import { InstallationBadge } from '@/components/marketplace/badges/installation-badge'
import { OriginBadge } from '@/components/marketplace/badges/origin-badge'
import { PublicationBadge } from '@/components/marketplace/badges/publication-badge'
import { SupportBadge } from '@/components/marketplace/badges/support-badge'
import { getResourceTone, resourceCardClassName } from '@/lib/resource-tones'
import type { MarketplaceItem } from '@/lib/types/marketplace'
import { formatMediumDate } from '@/lib/utils/format-relative-time'
import { cn } from '@/lib/utils'

export type PrimaryCtaKind =
  | 'install'
  | 'open'
  | 'setup'
  | 'update'
  | 'review_update'
  | 'view_details'
  | 'manage'
  | 'disabled'

export interface PrimaryCta {
  kind: PrimaryCtaKind
  variant: 'default' | 'outline'
  disabled?: boolean
}

export function derivePrimaryCta(item: MarketplaceItem): PrimaryCta {
  const installation = item.installation
  const supportLevel = item.execution_profile?.support_level
  const publicationState = item.publication_summary?.state

  if (item.status === 'disabled') {
    return { kind: 'disabled', variant: 'outline', disabled: true }
  }

  // Owner of a published item should not "install" their own source — the
  // skill row already exists on their side as the publication origin
  // (PRD §6: install creates a copy from someone else's marketplace version).
  // Route them to detail/management instead.
  if (publicationState && publicationState !== 'not_published' && !installation?.installed) {
    return { kind: 'manage', variant: 'outline' }
  }

  if (
    (supportLevel === 'manual_only' || supportLevel === 'browser_or_local') &&
    !installation?.installed
  ) {
    return { kind: 'view_details', variant: 'outline' }
  }

  if (!installation?.installed) {
    return { kind: 'install', variant: 'default' }
  }

  if (installation.status === 'needs_setup') {
    return { kind: 'setup', variant: 'default' }
  }

  if (installation.update_available && installation.dirty) {
    return { kind: 'review_update', variant: 'outline' }
  }

  if (installation.update_available) {
    return { kind: 'update', variant: 'default' }
  }

  if (installation.status === 'disabled') {
    return { kind: 'disabled', variant: 'outline', disabled: true }
  }

  return { kind: 'open', variant: 'outline' }
}

interface MarketplaceCardProps {
  item: MarketplaceItem
  onAction?: (item: MarketplaceItem, cta: PrimaryCta) => void
  className?: string
}

function MarketplaceCardInner({ item, onAction, className }: MarketplaceCardProps) {
  const t = useTranslations('marketplace.card')
  const cta = derivePrimaryCta(item)
  const ctaLabel = t(`cta.${cta.kind}`)
  const ownerLabel = item.is_system ? t('owner.system') : t('owner.community')
  const resourceLabel = t(`resource.${item.resource_type}`)
  const versionLabel = item.latest_version?.version_label
  const versionDate = item.latest_version?.created_at
  const tone = getResourceTone(item.resource_type)

  return (
    <Card
      size="sm"
      className={cn(
        resourceCardClassName(tone, 'h-full min-h-[184px] cursor-default py-4'),
        className,
      )}
    >
      <CardHeader className="flex flex-row items-start justify-between gap-3 rounded-t-md px-0 pb-0 pt-0">
        <div
          className={cn(
            'moldy-resource-icon',
            tone.icon,
          )}
        >
          <DomainIcon
            iconId={item.icon_id ?? getDomainIconIdForResource(item.resource_type)}
            className="size-5 text-current"
          />
        </div>
        <div className="shrink-0">
          <Button
            variant={cta.variant}
            size="sm"
            disabled={cta.disabled}
            onClick={() => onAction?.(item, cta)}
            aria-label={ctaLabel}
          >
            {ctaLabel}
          </Button>
        </div>
      </CardHeader>

      <CardContent className="flex flex-1 flex-col px-0 pb-0 pt-0">
        <Link
          href={`/marketplace/${item.id}`}
          className="mt-3 line-clamp-1 moldy-ui-card-title font-bold leading-tight text-foreground hover:text-primary-strong"
        >
          {item.name}
        </Link>
        <p className="mt-1 truncate text-xs text-muted-foreground">
          {ownerLabel} · {resourceLabel}
        </p>

        {item.description ? (
          <p className="mt-2 line-clamp-2 min-h-[2.65em] text-xs leading-[1.45] text-muted-foreground">
            {item.description}
          </p>
        ) : null}

        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          <OriginBadge summary={item.origin_summary} />
          <PublicationBadge summary={item.publication_summary} />
          <CredentialBadge summary={item.credential_summary} />
          <SupportBadge profile={item.execution_profile} />
        </div>

        <div className="mt-auto flex items-center justify-between pt-3 moldy-ui-caption text-muted-foreground">
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

export const MarketplaceCard = memo(MarketplaceCardInner)
MarketplaceCard.displayName = 'MarketplaceCard'
