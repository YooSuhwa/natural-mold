'use client'

import Link from 'next/link'
import { memo } from 'react'
import { ChevronRightIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button, buttonVariants } from '@/components/ui/button'
import { DomainIcon, getDomainIconIdForResource } from '@/components/shared/icon'
import { CredentialBadge } from '@/components/marketplace/badges/credential-badge'
import { InstallationBadge } from '@/components/marketplace/badges/installation-badge'
import { SupportBadge } from '@/components/marketplace/badges/support-badge'
import {
  ResourceBadge,
  ResourceCardMeta,
  ResourceListCard,
} from '@/components/shared/resource-layout'
import { getResourceTone } from '@/lib/resource-tones'
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

export function getPrimaryCtaHref(item: MarketplaceItem, cta: PrimaryCta): string | null {
  if (cta.disabled) return null
  if (cta.kind === 'open' && item.installation.installed_resource_id) {
    if (item.resource_type === 'skill') {
      return `/skills?detailId=${item.installation.installed_resource_id}`
    }
    if (item.resource_type === 'mcp') {
      return `/mcp-servers?detailId=${item.installation.installed_resource_id}`
    }
    if (item.resource_type === 'agent') {
      return `/agents/new/template?blueprintId=${item.installation.installed_resource_id}`
    }
  }
  if (cta.kind === 'view_details' || cta.kind === 'manage') {
    return `/marketplace/${item.id}`
  }
  return null
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
  const detailsHref = `/marketplace/${item.id}`
  const primaryHref = getPrimaryCtaHref(item, cta)
  const showDetailsLink = primaryHref !== detailsHref
  const showCredentialBadge = item.credential_summary && item.credential_summary.status !== 'none'
  const showSupportBadge =
    item.execution_profile?.support_level && item.execution_profile.support_level !== 'ready_python'
  const hasStatusSignals = Boolean(
    item.installation?.installed || showCredentialBadge || showSupportBadge,
  )
  const versionMeta = versionLabel
    ? `v${versionLabel}${versionDate ? ` · ${formatMediumDate(versionDate)}` : ''}`
    : null

  return (
    <ResourceListCard as="article" tone={tone} density="rich" className={cn('h-full', className)}>
      <ResourceListCard.Header>
        <div className={cn('moldy-resource-icon', tone.icon)}>
          <DomainIcon
            iconId={item.icon_id ?? getDomainIconIdForResource(item.resource_type)}
            className="size-5 text-current"
          />
        </div>
        <ResourceBadge tone={tone}>{resourceLabel}</ResourceBadge>
      </ResourceListCard.Header>

      <ResourceListCard.Title>{item.name}</ResourceListCard.Title>
      <ResourceListCard.Subhead>{ownerLabel}</ResourceListCard.Subhead>
      <ResourceListCard.Description>
        {item.description ?? resourceLabel}
      </ResourceListCard.Description>

      {hasStatusSignals ? (
        <ResourceListCard.StatusRow>
          <InstallationBadge summary={item.installation} />
          {showCredentialBadge ? (
            <CredentialBadge summary={item.credential_summary} />
          ) : showSupportBadge ? (
            <SupportBadge profile={item.execution_profile} />
          ) : null}
        </ResourceListCard.StatusRow>
      ) : null}

      <ResourceListCard.MetaRow>
        {versionMeta ? <ResourceCardMeta>{versionMeta}</ResourceCardMeta> : null}
      </ResourceListCard.MetaRow>

      <ResourceListCard.Footer className="justify-between">
        {showDetailsLink ? (
          <Link href={detailsHref} className={cn(buttonVariants({ variant: 'ghost', size: 'sm' }))}>
            {t('cta.view_details')}
          </Link>
        ) : (
          <span />
        )}
        {primaryHref ? (
          <Link
            href={primaryHref}
            className={cn(buttonVariants({ variant: cta.variant, size: 'sm' }))}
          >
            {ctaLabel}
            <ChevronRightIcon aria-hidden className="size-3.5" />
          </Link>
        ) : (
          <Button
            variant={cta.variant}
            size="sm"
            disabled={cta.disabled}
            onClick={() => onAction?.(item, cta)}
            aria-label={ctaLabel}
          >
            {ctaLabel}
          </Button>
        )}
      </ResourceListCard.Footer>
    </ResourceListCard>
  )
}

export const MarketplaceCard = memo(MarketplaceCardInner)
MarketplaceCard.displayName = 'MarketplaceCard'
