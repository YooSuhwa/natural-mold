'use client'

import Link from 'next/link'
import { memo } from 'react'
import { useTranslations } from 'next-intl'
import type { LucideIcon } from 'lucide-react'
import { BotIcon, ServerIcon, SparklesIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { CredentialBadge } from '@/components/marketplace/badges/credential-badge'
import { InstallationBadge } from '@/components/marketplace/badges/installation-badge'
import { OriginBadge } from '@/components/marketplace/badges/origin-badge'
import { PublicationBadge } from '@/components/marketplace/badges/publication-badge'
import { SupportBadge } from '@/components/marketplace/badges/support-badge'
import type { MarketplaceItem, MarketplaceResourceType } from '@/lib/types/marketplace'
import { formatMediumDate } from '@/lib/utils/format-relative-time'
import { cn } from '@/lib/utils'

const RESOURCE_ICONS: Record<MarketplaceResourceType, LucideIcon> = {
  skill: SparklesIcon,
  agent: BotIcon,
  mcp: ServerIcon,
}

type MarketplaceCardTone = {
  card: string
  icon: string
}

const MARKETPLACE_CARD_TONES: MarketplaceCardTone[] = [
  {
    card: 'bg-violet-50/75 hover:border-violet-200 dark:bg-violet-500/10 dark:hover:border-violet-400/30',
    icon: 'bg-violet-100 text-violet-700 ring-violet-200/70 dark:bg-violet-500/20 dark:text-violet-200 dark:ring-violet-400/20',
  },
  {
    card: 'bg-sky-50/75 hover:border-sky-200 dark:bg-sky-500/10 dark:hover:border-sky-400/30',
    icon: 'bg-sky-100 text-sky-700 ring-sky-200/70 dark:bg-sky-500/20 dark:text-sky-200 dark:ring-sky-400/20',
  },
  {
    card: 'bg-emerald-50/75 hover:border-emerald-200 dark:bg-emerald-500/10 dark:hover:border-emerald-400/30',
    icon: 'bg-emerald-100 text-emerald-700 ring-emerald-200/70 dark:bg-emerald-500/20 dark:text-emerald-200 dark:ring-emerald-400/20',
  },
  {
    card: 'bg-amber-50/75 hover:border-amber-200 dark:bg-amber-500/10 dark:hover:border-amber-400/30',
    icon: 'bg-amber-100 text-amber-700 ring-amber-200/70 dark:bg-amber-500/20 dark:text-amber-200 dark:ring-amber-400/20',
  },
  {
    card: 'bg-rose-50/75 hover:border-rose-200 dark:bg-rose-500/10 dark:hover:border-rose-400/30',
    icon: 'bg-rose-100 text-rose-700 ring-rose-200/70 dark:bg-rose-500/20 dark:text-rose-200 dark:ring-rose-400/20',
  },
]

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
  const Icon = RESOURCE_ICONS[item.resource_type] ?? SparklesIcon
  const cta = derivePrimaryCta(item)
  const ctaLabel = t(`cta.${cta.kind}`)
  const ownerLabel = item.is_system ? t('owner.system') : t('owner.community')
  const resourceLabel = t(`resource.${item.resource_type}`)
  const versionLabel = item.latest_version?.version_label
  const versionDate = item.latest_version?.created_at
  const tone = pickMarketplaceCardTone(item)

  return (
    <Card
      size="sm"
      className={cn(
        'group/card relative flex h-full min-h-[184px] flex-col rounded-md border border-transparent p-4 text-left',
        'shadow-[0_10px_24px_-22px_rgba(15,23,42,0.45)] ring-0 transition-all duration-150',
        'hover:-translate-y-px hover:shadow-[0_18px_32px_-24px_rgba(15,23,42,0.55)]',
        'focus-within:-translate-y-px focus-within:border-emerald-300 focus-within:shadow-md focus-within:ring-2 focus-within:ring-emerald-400/40',
        tone.card,
        className,
      )}
    >
      <CardHeader className="flex flex-row items-start justify-between gap-3 rounded-t-md px-0 pb-0 pt-0">
        <div
          className={cn(
            'flex size-9 shrink-0 items-center justify-center rounded-lg ring-1',
            tone.icon,
          )}
        >
          <Icon className="size-5" aria-hidden />
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
          className="mt-3 line-clamp-1 text-[15px] font-bold leading-tight text-foreground hover:text-primary-strong"
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

        <div className="mt-auto flex items-center justify-between pt-3 text-[11px] text-muted-foreground">
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

function pickMarketplaceCardTone(item: MarketplaceItem): MarketplaceCardTone {
  const seed = `${item.resource_type}:${item.slug}:${item.name}`
  let hash = 0
  for (let i = 0; i < seed.length; i += 1) hash += seed.charCodeAt(i)
  return MARKETPLACE_CARD_TONES[hash % MARKETPLACE_CARD_TONES.length]
}
