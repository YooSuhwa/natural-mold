'use client'

import { useMemo, useState, type KeyboardEvent } from 'react'
import { BookOpen, ChevronRightIcon, FileText, Package, Plus } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/shared/empty-state'
import { SearchInput } from '@/components/shared/search-input'
import {
  CountedLineTabs,
  ResourceGrid,
  ResourcePage,
  ResourcePanel,
  ResourceToolbar,
} from '@/components/shared/resource-layout'
import { Skeleton } from '@/components/ui/skeleton'
import { SkillCreateDialog } from '@/components/skill/skill-create-dialog'
import { SkillDetailDialog } from '@/components/skill/skill-detail-dialog'
import { OriginBadge } from '@/components/marketplace/badges/origin-badge'
import { PublicationBadge } from '@/components/marketplace/badges/publication-badge'
import { PublishWizard } from '@/components/marketplace/publish-wizard'
import { useSkills } from '@/lib/hooks/use-skills'
import type { Skill } from '@/lib/types/skill'
import { cn } from '@/lib/utils'

type CreateTab = 'text' | 'package' | 'scratch'
type SkillTab = 'all' | Skill['kind']

const ALL_TAB = 'all'

function formatDate(value: string | null): string {
  if (!value) return ''
  return new Date(value).toLocaleDateString()
}

export default function SkillsPage() {
  const t = useTranslations('skill')
  const { data: skills, isLoading } = useSkills()
  const [createOpen, setCreateOpen] = useState(false)
  const [createTab, setCreateTab] = useState<CreateTab>('text')
  const [activeTab, setActiveTab] = useState<SkillTab>(ALL_TAB)
  const [search, setSearch] = useState('')
  // Deep-link from /marketplace Open button: `/skills?detailId=...`.
  // useState lazy initializer runs once at mount (post-hydration on client,
  // safely returns null during SSR/prerender). Avoids effect+setState pattern
  // that the react-hooks/set-state-in-effect rule rejects.
  const [detailId, setDetailId] = useState<string | null>(() => {
    if (typeof window === 'undefined') return null
    return new URLSearchParams(window.location.search).get('detailId')
  })
  const [publishSkill, setPublishSkill] = useState<Skill | null>(null)

  function openCreate(tab: CreateTab) {
    setCreateTab(tab)
    setCreateOpen(true)
  }

  const data = useMemo(() => skills ?? [], [skills])
  const normalizedSearch = search.trim().toLowerCase()

  const filteredSkills = useMemo(() => {
    return data.filter((skill) => {
      if (activeTab !== ALL_TAB && skill.kind !== activeTab) return false
      if (!normalizedSearch) return true
      return [skill.name, skill.slug, skill.description, skill.version, skill.kind]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(normalizedSearch))
    })
  }, [activeTab, data, normalizedSearch])

  function countSkills(tab: SkillTab): number {
    return data.filter((skill) => {
      if (tab !== ALL_TAB && skill.kind !== tab) return false
      if (!normalizedSearch) return true
      return [skill.name, skill.slug, skill.description, skill.version, skill.kind]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(normalizedSearch))
    }).length
  }

  const tabs = ([ALL_TAB, 'text', 'package'] as SkillTab[]).map((value) => ({
    value,
    label: value === ALL_TAB ? t('typeFilter.all') : t(`typeFilter.${value}`),
    countLabel: t('count', { count: countSkills(value) }),
  }))

  const isInitialEmpty = !isLoading && data.length === 0
  const isFilteredEmpty = !isLoading && data.length > 0 && filteredSkills.length === 0

  return (
    <ResourcePage
      title={t('title')}
      description={t('description')}
      action={
        <Button onClick={() => openCreate('text')}>
          <Plus className="size-4" />
          {t('new')}
        </Button>
      }
    >
      <ResourcePanel>
        {isInitialEmpty ? (
          <ResourcePanel.Body>
            <EmptyState
              icon={<BookOpen className="size-6" />}
              title={t('empty.title')}
              description={t('empty.description')}
              className="bg-card/50"
              action={
                <Button onClick={() => openCreate('text')}>
                  <Plus className="size-4" />
                  {t('firstSkill')}
                </Button>
              }
            />
          </ResourcePanel.Body>
        ) : (
          <>
            <ResourcePanel.Toolbar>
              <CountedLineTabs
                ariaLabel={t('viewMode.label')}
                value={activeTab}
                tabs={tabs}
                onValueChange={(value) => setActiveTab(value as SkillTab)}
              />
              <ResourceToolbar>
                <SearchInput
                  containerClassName="flex-1 sm:max-w-[360px]"
                  placeholder={t('searchPlaceholder')}
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                />
              </ResourceToolbar>
            </ResourcePanel.Toolbar>

            <ResourcePanel.Body className="bg-background/30">
              {isLoading ? (
                <ResourceGrid minColumnWidth={240}>
                  {Array.from({ length: 6 }).map((_, index) => (
                    <Skeleton key={index} className="h-[176px] rounded-md" />
                  ))}
                </ResourceGrid>
              ) : isFilteredEmpty ? (
                <EmptyState title={t('empty.filtered')} className="bg-card/50" />
              ) : (
                <ResourceGrid minColumnWidth={240}>
                  {filteredSkills.map((skill) => (
                    <SkillCard
                      key={skill.id}
                      skill={skill}
                      kindLabel={t(`typeFilter.${skill.kind}`)}
                      agentsLabel={t('agentsCount', { count: skill.used_by_count })}
                      updatedLabel={formatDate(skill.updated_at)}
                      actionLabel={t('actions.manage')}
                      publishLabel={t('actions.publish')}
                      onOpen={setDetailId}
                      onPublish={setPublishSkill}
                    />
                  ))}
                </ResourceGrid>
              )}
            </ResourcePanel.Body>
          </>
        )}
      </ResourcePanel>

      <SkillCreateDialog
        key={`create-${createTab}`}
        open={createOpen}
        onOpenChange={setCreateOpen}
        initialTab={createTab}
        onCreated={(id) => setDetailId(id)}
      />
      <SkillDetailDialog
        skillId={detailId}
        open={!!detailId}
        onOpenChange={(open) => {
          if (open) return
          setDetailId(null)
          // /marketplace에서 ``?detailId=...`` deep-link로 진입한 경우, dialog
          // 닫을 때 URL의 query string도 함께 정리한다. history.replaceState로
          // route 자체는 다시 그리지 않아 list scroll/state 보존.
          if (typeof window !== 'undefined' && window.location.search) {
            window.history.replaceState(null, '', '/skills')
          }
        }}
      />

      <PublishWizard
        skill={publishSkill}
        open={!!publishSkill}
        onOpenChange={(open) => !open && setPublishSkill(null)}
      />
    </ResourcePage>
  )
}

function SkillCard({
  skill,
  kindLabel,
  agentsLabel,
  updatedLabel,
  actionLabel,
  publishLabel,
  onOpen,
  onPublish,
}: {
  skill: Skill
  kindLabel: string
  agentsLabel: string
  updatedLabel: string
  actionLabel: string
  publishLabel: string
  onOpen: (id: string) => void
  onPublish: (skill: Skill) => void
}) {
  const tone = pickSkillCardTone(`${skill.kind}:${skill.slug}:${skill.name}`)
  const Icon = skill.kind === 'package' ? Package : FileText
  const canPublish =
    !skill.publication_summary?.state || skill.publication_summary.state === 'not_published'

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== 'Enter' && event.key !== ' ') return
    event.preventDefault()
    onOpen(skill.id)
  }

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onOpen(skill.id)}
      onKeyDown={handleKeyDown}
      className={cn(skillCardClassName(tone))}
    >
      <div className="flex items-start justify-between gap-3">
        <span
          className={cn(
            'inline-flex size-9 shrink-0 items-center justify-center rounded-lg',
            tone.icon,
          )}
        >
          <Icon className="size-4.5" />
        </span>
        <span
          className={cn(
            'inline-flex min-w-0 max-w-[120px] items-center gap-1 rounded-md border px-2 py-1 text-[11px] font-semibold leading-none',
            tone.badge,
          )}
        >
          <span className={cn('size-1.5 shrink-0 rounded-full', tone.dot)} />
          <span className="truncate">{kindLabel}</span>
        </span>
      </div>

      <span className="mt-3 line-clamp-1 text-[15px] font-bold leading-tight text-foreground">
        {skill.name}
      </span>
      <p className="mt-2 line-clamp-2 min-h-[2.65em] text-xs leading-[1.45] text-muted-foreground">
        {skill.description ?? skill.slug}
      </p>

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        {skill.version ? <span className={skillMetaClassName}>v{skill.version}</span> : null}
        <span className={skillMetaClassName}>{agentsLabel}</span>
        {updatedLabel ? <span className={skillMetaClassName}>{updatedLabel}</span> : null}
      </div>
      <p className="mt-2 truncate font-mono text-[11px] text-muted-foreground/80">{skill.slug}</p>

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <OriginBadge summary={skill.origin_summary} />
        <PublicationBadge summary={skill.publication_summary} />
      </div>

      <div className="mt-auto flex items-center justify-between gap-2 pt-3">
        {canPublish ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 px-2 text-xs"
            onClick={(event) => {
              event.stopPropagation()
              onPublish(skill)
            }}
          >
            {publishLabel}
          </Button>
        ) : (
          <span />
        )}
        <span
          className={cn(
            'inline-flex items-center gap-0.5 text-xs font-semibold text-muted-foreground transition-all duration-150',
            'group-hover:translate-x-0.5 group-hover:text-[var(--primary-strong)]',
            'group-focus-visible:translate-x-0.5 group-focus-visible:text-[var(--primary-strong)]',
          )}
        >
          {actionLabel}
          <ChevronRightIcon aria-hidden className="size-3" />
        </span>
      </div>
    </div>
  )
}

type SkillCardTone = {
  card: string
  icon: string
  badge: string
  dot: string
}

const SKILL_CARD_TONES: SkillCardTone[] = [
  {
    card: 'bg-violet-50/75 hover:border-violet-200 dark:bg-violet-500/10 dark:hover:border-violet-400/30',
    icon: 'bg-violet-100 text-violet-700 dark:bg-violet-500/20 dark:text-violet-200',
    badge:
      'border-violet-100 bg-white/70 text-violet-800 dark:border-violet-400/20 dark:bg-violet-500/10 dark:text-violet-200',
    dot: 'bg-violet-500',
  },
  {
    card: 'bg-sky-50/75 hover:border-sky-200 dark:bg-sky-500/10 dark:hover:border-sky-400/30',
    icon: 'bg-sky-100 text-sky-700 dark:bg-sky-500/20 dark:text-sky-200',
    badge:
      'border-sky-100 bg-white/70 text-sky-800 dark:border-sky-400/20 dark:bg-sky-500/10 dark:text-sky-200',
    dot: 'bg-sky-500',
  },
  {
    card: 'bg-emerald-50/75 hover:border-emerald-200 dark:bg-emerald-500/10 dark:hover:border-emerald-400/30',
    icon: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-200',
    badge:
      'border-emerald-100 bg-white/70 text-emerald-800 dark:border-emerald-400/20 dark:bg-emerald-500/10 dark:text-emerald-200',
    dot: 'bg-emerald-500',
  },
  {
    card: 'bg-amber-50/75 hover:border-amber-200 dark:bg-amber-500/10 dark:hover:border-amber-400/30',
    icon: 'bg-amber-100 text-amber-700 dark:bg-amber-500/20 dark:text-amber-200',
    badge:
      'border-amber-100 bg-white/70 text-amber-800 dark:border-amber-400/20 dark:bg-amber-500/10 dark:text-amber-200',
    dot: 'bg-amber-500',
  },
  {
    card: 'bg-rose-50/75 hover:border-rose-200 dark:bg-rose-500/10 dark:hover:border-rose-400/30',
    icon: 'bg-rose-100 text-rose-700 dark:bg-rose-500/20 dark:text-rose-200',
    badge:
      'border-rose-100 bg-white/70 text-rose-800 dark:border-rose-400/20 dark:bg-rose-500/10 dark:text-rose-200',
    dot: 'bg-rose-500',
  },
]

const skillMetaClassName =
  'inline-flex max-w-[140px] items-center rounded border border-white/80 bg-white/55 px-1.5 py-0.5 text-[10.5px] font-semibold leading-none text-foreground shadow-sm dark:border-white/10 dark:bg-white/10'

function skillCardClassName(tone: SkillCardTone): string {
  return cn(
    'group relative flex min-h-[188px] cursor-pointer flex-col rounded-md border border-transparent p-4 text-left',
    'shadow-[0_10px_24px_-22px_rgba(15,23,42,0.45)] transition-all duration-150',
    'hover:-translate-y-px hover:shadow-[0_18px_32px_-24px_rgba(15,23,42,0.55)]',
    'focus-visible:-translate-y-px focus-visible:border-emerald-300 focus-visible:shadow-md',
    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/40',
    tone.card,
  )
}

function pickSkillCardTone(seed: string): SkillCardTone {
  let hash = 0
  for (let i = 0; i < seed.length; i += 1) hash += seed.charCodeAt(i)
  return SKILL_CARD_TONES[hash % SKILL_CARD_TONES.length]
}
