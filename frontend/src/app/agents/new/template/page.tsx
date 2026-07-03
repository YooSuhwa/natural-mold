'use client'

import { type ReactNode, useDeferredValue, useEffect, useMemo, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import Link from 'next/link'
import {
  ArrowUpDownIcon,
  BookOpenIcon,
  BotIcon,
  ChevronRightIcon,
  Loader2Icon,
  PlusIcon,
  SearchIcon,
  SparklesIcon,
  WrenchIcon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'
import { useTemplates } from '@/lib/hooks/use-templates'
import { useCreateAgent } from '@/lib/hooks/use-agents'
import { useModels } from '@/lib/hooks/use-models'
import { useAgentBlueprints, useCreateAgentFromBlueprint } from '@/lib/hooks/use-marketplace'
import { EmptyState } from '@/components/shared/empty-state'
import {
  CountedLineTabs,
  ResourceBadge,
  ResourceCardMeta,
  ResourceGrid,
  ResourceListCard,
  ResourcePage,
  ResourcePanel,
  ResourceToolbar,
} from '@/components/shared/resource-layout'
import { Skeleton } from '@/components/ui/skeleton'
import { getResourceTone } from '@/lib/resource-tones'
import { cn } from '@/lib/utils'
import type { Template } from '@/lib/types'
import type { AgentBlueprint } from '@/lib/types/marketplace'

type SortKey = 'newest' | 'name'

const CATEGORIES: { value: string; labelKey: string }[] = [
  { value: '', labelKey: 'category.all' },
  { value: 'category.productivityValue', labelKey: 'category.productivity' },
  { value: 'category.communicationValue', labelKey: 'category.communication' },
  { value: 'category.dataValue', labelKey: 'category.data' },
]

export default function TemplateSelectionPage() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const t = useTranslations('agent.template')
  const deepLinkedBlueprintId = searchParams.get('blueprintId')
  const [selectedCategory, setSelectedCategory] = useState('')
  const categoryValue = selectedCategory ? t(selectedCategory) : ''
  const [search, setSearch] = useState('')
  const deferredSearch = useDeferredValue(search)
  const [sortBy, setSortBy] = useState<SortKey>('newest')
  const [creatingId, setCreatingId] = useState<string | null>(null)
  const [creatingBlueprintId, setCreatingBlueprintId] = useState<string | null>(null)

  const { data: templates, isLoading } = useTemplates(categoryValue || undefined)
  const { data: blueprints, isLoading: isLoadingBlueprints } = useAgentBlueprints()
  const { data: models } = useModels()
  const createAgent = useCreateAgent()
  const createAgentFromBlueprint = useCreateAgentFromBlueprint()

  const defaultModelId = models?.find((m) => m.is_default)?.id ?? models?.[0]?.id ?? ''

  useEffect(() => {
    if (!deepLinkedBlueprintId || isLoadingBlueprints) return
    const target = document.getElementById(`agent-blueprint-${deepLinkedBlueprintId}`)
    if (!target) return
    target.scrollIntoView({ block: 'center' })
    target.focus({ preventScroll: true })
  }, [deepLinkedBlueprintId, isLoadingBlueprints, blueprints])

  const filtered = useMemo(() => {
    if (!templates) return [] as Template[]
    const q = deferredSearch.trim().toLowerCase()
    let list = templates
    if (q) {
      list = list.filter(
        (tpl) =>
          tpl.name.toLowerCase().includes(q) ||
          (tpl.description ?? '').toLowerCase().includes(q) ||
          (tpl.recommended_tools ?? []).some((tool) => tool.toLowerCase().includes(q)) ||
          (tpl.recommended_skill_slugs ?? []).some((skill) => skill.toLowerCase().includes(q)),
      )
    }
    return [...list].sort((a, b) => {
      if (sortBy === 'name') return a.name.localeCompare(b.name, 'ko')
      return b.created_at.localeCompare(a.created_at)
    })
  }, [templates, deferredSearch, sortBy])

  const filteredBlueprints = useMemo(() => {
    if (!blueprints) return [] as AgentBlueprint[]
    const q = deferredSearch.trim().toLowerCase()
    let list = blueprints
    if (categoryValue) {
      const category = categoryValue.toLowerCase()
      list = list.filter((blueprint) =>
        (blueprint.categories ?? []).some((value) => value.toLowerCase() === category),
      )
    }
    if (q) {
      list = list.filter(
        (blueprint) =>
          blueprint.name.toLowerCase().includes(q) ||
          (blueprint.description ?? '').toLowerCase().includes(q) ||
          (blueprint.tags ?? []).some((tag) => tag.toLowerCase().includes(q)) ||
          (blueprint.categories ?? []).some((category) => category.toLowerCase().includes(q)),
      )
    }
    return [...list].sort((a, b) => {
      if (sortBy === 'name') return a.name.localeCompare(b.name, 'ko')
      return b.created_at.localeCompare(a.created_at)
    })
  }, [blueprints, categoryValue, deferredSearch, sortBy])

  async function handleCreateFromTemplate(template: Template) {
    if (creatingId || creatingBlueprintId) return
    setCreatingId(template.id)
    try {
      const agent = await createAgent.mutateAsync({
        name: template.name,
        description: template.description ?? undefined,
        system_prompt: template.system_prompt,
        model_id: template.recommended_model_id ?? defaultModelId,
        identity_mode: 'per_user',
        template_id: template.id,
      })
      router.push(`/agents/${agent.id}`)
    } catch {
      setCreatingId(null)
    }
  }

  async function handleCreateFromBlueprint(blueprint: AgentBlueprint) {
    if (creatingId || creatingBlueprintId) return
    setCreatingBlueprintId(blueprint.id)
    try {
      const agent = await createAgentFromBlueprint.mutateAsync({
        blueprintId: blueprint.id,
        body: {
          name: blueprint.name,
        },
      })
      router.push(`/agents/${agent.id}`)
    } catch {
      setCreatingBlueprintId(null)
    }
  }

  const hasSearch = search.trim().length > 0
  const isLoadingGallery = isLoading || isLoadingBlueprints
  const filteredCount = filtered.length + filteredBlueprints.length
  const showNoSearchResults = !isLoadingGallery && hasSearch && filteredCount === 0
  const showEmptyCategory = !isLoadingGallery && !hasSearch && filteredCount === 0
  const galleryCountLabel = t('gallery.count', { count: filteredCount })
  const creatingLocked = creatingId !== null || creatingBlueprintId !== null

  return (
    <ResourcePage
      title={t('pageTitle')}
      description={t('subtitle')}
      action={<CreateConversationLink label={t('bottomCta.action')} />}
    >
      <ResourcePanel>
        <ResourcePanel.Toolbar>
          <FiltersBar
            selectedCategory={selectedCategory}
            onCategoryChange={setSelectedCategory}
            countLabel={galleryCountLabel}
            search={search}
            onSearchChange={setSearch}
            onSortToggle={() => setSortBy((s) => (s === 'newest' ? 'name' : 'newest'))}
            searchPlaceholder={t('search.placeholder')}
            searchAriaLabel={t('search.ariaLabel')}
            sortLabel={sortBy === 'newest' ? t('sort.newest') : t('sort.name')}
            sortAriaLabel={
              sortBy === 'newest' ? t('sort.ariaLabelNewest') : t('sort.ariaLabelName')
            }
            categoryAriaLabel={t('category.ariaLabel')}
            getCategoryLabel={(key) => t(key)}
          />
        </ResourcePanel.Toolbar>

        <ResourcePanel.Body>
          {isLoadingGallery ? (
            <TemplateGridSkeleton />
          ) : showNoSearchResults ? (
            <NoSearchResults
              title={t('noSearchResults.title', { query: search.trim() })}
              subtitle={t('noSearchResults.subtitle')}
            />
          ) : showEmptyCategory ? (
            <EmptyState
              icon={<SearchIcon className="size-6" />}
              title={t('emptyCategory')}
              className="bg-card/50"
            />
          ) : (
            <div className="space-y-6">
              {filteredBlueprints.length > 0 ? (
                <GallerySection
                  title={t('blueprints.title')}
                  description={t('blueprints.description')}
                >
                  <ResourceGrid minColumnWidth={252}>
                    {filteredBlueprints.map((blueprint) => (
                      <BlueprintCard
                        key={blueprint.id}
                        blueprint={blueprint}
                        isCreating={creatingBlueprintId === blueprint.id}
                        creatingLocked={creatingLocked}
                        isDeepLinked={blueprint.id === deepLinkedBlueprintId}
                        onSelect={handleCreateFromBlueprint}
                        ariaLabel={t('blueprints.create')}
                        startLabel={t('startCta')}
                        badgeLabel={t('blueprints.badge')}
                        createdCountFormatter={(count) => t('blueprints.createdCount', { count })}
                      />
                    ))}
                  </ResourceGrid>
                </GallerySection>
              ) : null}

              {filtered.length > 0 ? (
                <GallerySection title={t('legacy.title')} description={t('legacy.description')}>
                  <ResourceGrid minColumnWidth={252}>
                    {filtered.map((tpl) => (
                      <TemplateCard
                        key={tpl.id}
                        template={tpl}
                        isCreating={creatingId === tpl.id}
                        creatingLocked={creatingLocked}
                        onSelect={handleCreateFromTemplate}
                        ariaLabel={t('createFromTemplate')}
                        startLabel={t('startCta')}
                        toolsMoreFormatter={(count) => t('toolsMore', { count })}
                      />
                    ))}
                  </ResourceGrid>
                </GallerySection>
              ) : null}
            </div>
          )}
        </ResourcePanel.Body>
      </ResourcePanel>

      <BottomCta
        title={t('bottomCta.title')}
        subtitle={t('bottomCta.subtitle')}
        action={t('bottomCta.action')}
      />
    </ResourcePage>
  )
}

// ───────────────────────────────────────────────────────────── Header action

function CreateConversationLink({ label }: { label: string }) {
  return (
    <Link href="/agents/new" className={cn('moldy-primary-action')}>
      <PlusIcon aria-hidden className="size-4" />
      {label}
    </Link>
  )
}

// ───────────────────────────────────────────────────────── Filters bar

type FiltersBarProps = {
  selectedCategory: string
  onCategoryChange: (value: string) => void
  countLabel: string
  search: string
  onSearchChange: (value: string) => void
  onSortToggle: () => void
  searchPlaceholder: string
  searchAriaLabel: string
  sortLabel: string
  sortAriaLabel: string
  categoryAriaLabel: string
  getCategoryLabel: (key: string) => string
}

function FiltersBar({
  selectedCategory,
  onCategoryChange,
  countLabel,
  search,
  onSearchChange,
  onSortToggle,
  searchPlaceholder,
  searchAriaLabel,
  sortLabel,
  sortAriaLabel,
  categoryAriaLabel,
  getCategoryLabel,
}: FiltersBarProps) {
  const tabs = CATEGORIES.map((cat) => ({
    value: cat.value,
    label: getCategoryLabel(cat.labelKey),
    countLabel,
  }))

  return (
    <div className="flex flex-col gap-3">
      <CountedLineTabs
        ariaLabel={categoryAriaLabel}
        value={selectedCategory}
        tabs={tabs}
        onValueChange={onCategoryChange}
      />

      <ResourceToolbar>
        <div className="relative flex-1 sm:max-w-sm">
          <SearchIcon
            aria-hidden
            className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
          />
          <input
            type="search"
            value={search}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder={searchPlaceholder}
            aria-label={searchAriaLabel}
            className={cn(
              'moldy-search-input h-9 w-full border border-input bg-background pl-9 pr-3 text-sm text-foreground outline-hidden',
              'placeholder:text-muted-foreground',
            )}
          />
        </div>
        <button
          type="button"
          onClick={onSortToggle}
          aria-label={sortAriaLabel}
          className={cn(
            'inline-flex h-9 shrink-0 items-center gap-1.5 rounded-lg border border-input bg-background px-3 text-sm text-foreground transition-colors',
            'hover:bg-muted/50',
          )}
        >
          <ArrowUpDownIcon className="size-3.5 text-muted-foreground" />
          {sortLabel}
        </button>
      </ResourceToolbar>
    </div>
  )
}

function GallerySection({
  title,
  description,
  children,
}: {
  title: string
  description: string
  children: ReactNode
}) {
  return (
    <section className="space-y-3">
      <div className="flex flex-col gap-1">
        <h2 className="text-sm font-semibold text-foreground">{title}</h2>
        <p className="text-xs text-muted-foreground">{description}</p>
      </div>
      {children}
    </section>
  )
}

// ─────────────────────────────────────────────────────── Template card

type TemplateCardProps = {
  template: Template
  isCreating: boolean
  creatingLocked: boolean
  onSelect: (template: Template) => void
  ariaLabel: string
  startLabel: string
  toolsMoreFormatter: (count: number) => string
}

function TemplateCard({
  template,
  isCreating,
  creatingLocked,
  onSelect,
  ariaLabel,
  startLabel,
  toolsMoreFormatter,
}: TemplateCardProps) {
  const capabilities = [
    ...(template.recommended_tools ?? []).map((name) => ({ kind: 'tool' as const, name })),
    ...(template.recommended_skill_slugs ?? []).map((name) => ({ kind: 'skill' as const, name })),
  ]
  const visibleCapabilities = capabilities.slice(0, 2)
  const extraCapabilityCount = capabilities.length - visibleCapabilities.length
  const tone = getResourceTone(template.category)

  const disabled = creatingLocked && !isCreating

  return (
    <ResourceListCard
      as="button"
      tone={tone}
      density="compact"
      onClick={() => onSelect(template)}
      disabled={isCreating || disabled}
      aria-label={`${template.name} — ${ariaLabel}`}
      className={cn(
        isCreating && 'pointer-events-none opacity-70',
        disabled && 'pointer-events-none opacity-50',
      )}
    >
      <ResourceListCard.Header>
        <span className={cn('moldy-resource-icon', tone.icon)}>
          <BotIcon aria-hidden className="size-4.5" />
        </span>
        <ResourceBadge tone={tone}>{template.category}</ResourceBadge>
      </ResourceListCard.Header>

      <ResourceListCard.Title>{template.name}</ResourceListCard.Title>
      <ResourceListCard.Description>
        {template.description ?? template.category}
      </ResourceListCard.Description>

      {capabilities.length > 0 && (
        <ResourceListCard.MetaRow>
          {visibleCapabilities.map((capability) => (
            <ResourceCardMeta
              key={`${capability.kind}-${capability.name}`}
              className="max-w-24 gap-1"
            >
              {capability.kind === 'skill' ? (
                <BookOpenIcon aria-hidden className="size-2.5 text-muted-foreground" />
              ) : (
                <WrenchIcon aria-hidden className="size-2.5 text-muted-foreground" />
              )}
              <span className="truncate leading-none">{capability.name}</span>
            </ResourceCardMeta>
          ))}
          {extraCapabilityCount > 0 && (
            <ResourceCardMeta>{toolsMoreFormatter(extraCapabilityCount)}</ResourceCardMeta>
          )}
        </ResourceListCard.MetaRow>
      )}

      <ResourceListCard.Footer>
        {isCreating ? (
          <Loader2Icon className="size-3.5 animate-spin text-muted-foreground" />
        ) : (
          <span className="moldy-resource-action">
            {startLabel}
            <ChevronRightIcon aria-hidden className="size-3" />
          </span>
        )}
      </ResourceListCard.Footer>
    </ResourceListCard>
  )
}

type BlueprintCardProps = {
  blueprint: AgentBlueprint
  isCreating: boolean
  creatingLocked: boolean
  isDeepLinked: boolean
  onSelect: (blueprint: AgentBlueprint) => void
  ariaLabel: string
  startLabel: string
  badgeLabel: string
  createdCountFormatter: (count: number) => string
}

function BlueprintCard({
  blueprint,
  isCreating,
  creatingLocked,
  isDeepLinked,
  onSelect,
  ariaLabel,
  startLabel,
  badgeLabel,
  createdCountFormatter,
}: BlueprintCardProps) {
  const categories = blueprint.categories ?? []
  const visibleCategories = categories.slice(0, 2)
  const tone = getResourceTone(categories[0] ?? 'agent')
  const disabled = creatingLocked && !isCreating

  return (
    <ResourceListCard
      id={`agent-blueprint-${blueprint.id}`}
      as="button"
      tone={tone}
      density="compact"
      onClick={() => onSelect(blueprint)}
      disabled={isCreating || disabled}
      aria-label={`${blueprint.name} — ${ariaLabel}`}
      aria-current={isDeepLinked ? 'true' : undefined}
      className={cn(
        isDeepLinked && 'border-primary/70',
        isCreating && 'pointer-events-none opacity-70',
        disabled && 'pointer-events-none opacity-50',
      )}
    >
      <ResourceListCard.Header>
        <span className={cn('moldy-resource-icon', tone.icon)}>
          <BotIcon aria-hidden className="size-4.5" />
        </span>
        <ResourceBadge tone={tone}>{badgeLabel}</ResourceBadge>
      </ResourceListCard.Header>

      <ResourceListCard.Title>{blueprint.name}</ResourceListCard.Title>
      <ResourceListCard.Description>
        {blueprint.description ?? badgeLabel}
      </ResourceListCard.Description>

      {(visibleCategories.length > 0 || blueprint.created_agent_count > 0) && (
        <ResourceListCard.MetaRow>
          {visibleCategories.map((category) => (
            <ResourceCardMeta key={category}>{category}</ResourceCardMeta>
          ))}
          {blueprint.created_agent_count > 0 ? (
            <ResourceCardMeta>
              {createdCountFormatter(blueprint.created_agent_count)}
            </ResourceCardMeta>
          ) : null}
        </ResourceListCard.MetaRow>
      )}

      <ResourceListCard.Footer>
        {isCreating ? (
          <Loader2Icon className="size-3.5 animate-spin text-muted-foreground" />
        ) : (
          <span className="moldy-resource-action">
            {startLabel}
            <ChevronRightIcon aria-hidden className="size-3" />
          </span>
        )}
      </ResourceListCard.Footer>
    </ResourceListCard>
  )
}

// ───────────────────────────────────────────────────────── No search results

function NoSearchResults({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="moldy-muted-panel flex flex-col items-center gap-2 px-6 py-12 text-center">
      <SearchIcon aria-hidden className="size-7 text-muted-foreground/50" />
      <div className="text-sm font-semibold text-foreground">{title}</div>
      <div className="text-xs text-muted-foreground">{subtitle}</div>
    </div>
  )
}

// ───────────────────────────────────────────────────────── Grid skeleton

function TemplateGridSkeleton() {
  return (
    <ResourceGrid minColumnWidth={252}>
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="moldy-card flex min-h-40 flex-col p-4">
          <div className="flex items-center justify-between">
            <Skeleton className="size-9 rounded-lg" />
            <Skeleton className="h-5 w-20 rounded-md" />
          </div>
          <Skeleton className="mt-3 h-4 w-32" />
          <Skeleton className="mt-2 h-3 w-full" />
          <Skeleton className="mt-1.5 h-3 w-4/5" />
          <div className="mt-3 flex gap-1.5">
            <Skeleton className="h-4 w-16 rounded" />
            <Skeleton className="h-4 w-14 rounded" />
          </div>
          <div className="mt-3.5 flex justify-end border-t border-dashed border-border pt-3">
            <Skeleton className="h-3 w-10" />
          </div>
        </div>
      ))}
    </ResourceGrid>
  )
}

// ───────────────────────────────────────────────────────── Bottom CTA

function BottomCta({
  title,
  subtitle,
  action,
}: {
  title: string
  subtitle: string
  action: string
}) {
  return (
    <Link
      href="/agents/new"
      className={cn(
        'moldy-dashboard-action group mt-auto flex shrink-0 items-center gap-4 rounded-lg p-4',
      )}
    >
      <span className="inline-flex size-10 shrink-0 items-center justify-center rounded-lg border border-border bg-card moldy-color-primary-strong">
        <SparklesIcon className="size-5" />
      </span>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-semibold text-foreground">{title}</div>
        <div className="mt-0.5 text-xs text-muted-foreground">{subtitle}</div>
      </div>
      <span className="inline-flex shrink-0 items-center gap-0.5 text-sm font-semibold moldy-color-primary-strong transition-transform group-hover:translate-x-0.5">
        {action}
        <ChevronRightIcon aria-hidden className="size-3.5" />
      </span>
    </Link>
  )
}
