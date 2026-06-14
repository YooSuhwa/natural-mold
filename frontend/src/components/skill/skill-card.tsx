'use client'

import { ChevronRightIcon, FileText, Package } from 'lucide-react'

import { OriginBadge } from '@/components/marketplace/badges/origin-badge'
import { PublicationBadge } from '@/components/marketplace/badges/publication-badge'
import {
  ResourceBadge,
  ResourceCardMeta,
  ResourceListCard,
} from '@/components/shared/resource-layout'
import { Button } from '@/components/ui/button'
import { getResourceTone } from '@/lib/resource-tones'
import type { Skill } from '@/lib/types/skill'
import { cn } from '@/lib/utils'

import { SkillEvaluationSummaryBadge } from './skill-evaluation-summary-badge'
import { SkillHealthBadge } from './skill-health-badge'

type SkillCardProps = {
  readonly skill: Skill
  readonly kindLabel: string
  readonly agentsLabel: string
  readonly updatedLabel: string
  readonly actionLabel: string
  readonly publishLabel: string
  readonly onOpen: (id: string) => void
  readonly onPublish: (skill: Skill) => void
}

export function SkillCard({
  skill,
  kindLabel,
  agentsLabel,
  updatedLabel,
  actionLabel,
  publishLabel,
  onOpen,
  onPublish,
}: SkillCardProps) {
  const tone = getResourceTone(skill.kind)
  const Icon = skill.kind === 'package' ? Package : FileText
  const canPublish =
    !skill.publication_summary?.state || skill.publication_summary.state === 'not_published'
  const metaLabels = [
    skill.version ? `v${skill.version}` : null,
    agentsLabel,
    skill.version ? null : updatedLabel,
  ]
    .filter((label): label is string => Boolean(label))
    .slice(0, 2)
  const hasMarketplaceSignals = Boolean(skill.origin_summary || skill.publication_summary)
  const hasQualitySignals = Boolean(skill.health || skill.latest_evaluation_summary)

  return (
    <ResourceListCard as="article" tone={tone} density="rich">
      <ResourceListCard.Header>
        <span className={cn('moldy-resource-icon', tone.icon)}>
          <Icon className="size-4.5" />
        </span>
        <ResourceBadge tone={tone}>{kindLabel}</ResourceBadge>
      </ResourceListCard.Header>

      <ResourceListCard.Title>{skill.name}</ResourceListCard.Title>
      <ResourceListCard.Subhead tone="mono">{skill.slug}</ResourceListCard.Subhead>
      <ResourceListCard.Description>{skill.description ?? skill.slug}</ResourceListCard.Description>

      {hasQualitySignals ? (
        <ResourceListCard.StatusRow>
          <SkillHealthBadge health={skill.health} />
          <SkillEvaluationSummaryBadge summary={skill.latest_evaluation_summary} />
        </ResourceListCard.StatusRow>
      ) : null}

      {hasMarketplaceSignals ? (
        <ResourceListCard.StatusRow>
          <OriginBadge summary={skill.origin_summary} />
          <PublicationBadge summary={skill.publication_summary} />
        </ResourceListCard.StatusRow>
      ) : null}

      <ResourceListCard.MetaRow>
        {metaLabels.map((label) => (
          <ResourceCardMeta key={label}>{label}</ResourceCardMeta>
        ))}
      </ResourceListCard.MetaRow>

      <ResourceListCard.Footer className="justify-between">
        {canPublish ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 px-2 text-xs"
            onClick={() => onPublish(skill)}
          >
            {publishLabel}
          </Button>
        ) : (
          <span />
        )}
        <Button type="button" variant="outline" size="sm" onClick={() => onOpen(skill.id)}>
          {actionLabel}
          <ChevronRightIcon aria-hidden className="size-3" />
        </Button>
      </ResourceListCard.Footer>
    </ResourceListCard>
  )
}
