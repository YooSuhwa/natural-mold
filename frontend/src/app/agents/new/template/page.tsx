'use client'

import { useDeferredValue, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  ArrowUpDownIcon,
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
import {
  getResourceTone,
} from '@/lib/resource-tones'
import { cn } from '@/lib/utils'
import type { Template } from '@/lib/types'

type SortKey = 'newest' | 'name'

const CATEGORIES: { value: string; labelKey: string }[] = [
  { value: '', labelKey: 'category.all' },
  { value: 'category.productivityValue', labelKey: 'category.productivity' },
  { value: 'category.communicationValue', labelKey: 'category.communication' },
  { value: 'category.dataValue', labelKey: 'category.data' },
]

export default function TemplateSelectionPage() {
  const router = useRouter()
  const t = useTranslations('agent.template')
  const [selectedCategory, setSelectedCategory] = useState('')
  const categoryValue = selectedCategory ? t(selectedCategory) : ''
  const [search, setSearch] = useState('')
  const deferredSearch = useDeferredValue(search)
  const [sortBy, setSortBy] = useState<SortKey>('newest')
  const [creatingId, setCreatingId] = useState<string | null>(null)

  const { data: templates, isLoading } = useTemplates(categoryValue || undefined)
  const { data: models } = useModels()
  const createAgent = useCreateAgent()

  const defaultModelId = models?.find((m) => m.is_default)?.id ?? models?.[0]?.id ?? ''

  const filtered = useMemo(() => {
    if (!templates) return [] as Template[]
    const q = deferredSearch.trim().toLowerCase()
    let list = templates
    if (q) {
      list = list.filter(
        (tpl) =>
          tpl.name.toLowerCase().includes(q) ||
          (tpl.description ?? '').toLowerCase().includes(q) ||
          (tpl.recommended_tools ?? []).some((tool) => tool.toLowerCase().includes(q)),
      )
    }
    return [...list].sort((a, b) => {
      if (sortBy === 'name') return a.name.localeCompare(b.name, 'ko')
      return b.created_at.localeCompare(a.created_at)
    })
  }, [templates, deferredSearch, sortBy])

  async function handleCreateFromTemplate(template: Template) {
    if (creatingId) return
    setCreatingId(template.id)
    try {
      const agent = await createAgent.mutateAsync({
        name: template.name,
        description: template.description ?? undefined,
        system_prompt: template.system_prompt,
        model_id: template.recommended_model_id ?? defaultModelId,
        template_id: template.id,
      })
      router.push(`/agents/${agent.id}`)
    } catch {
      setCreatingId(null)
    }
  }

  const hasSearch = search.trim().length > 0
  const showNoSearchResults = !isLoading && hasSearch && filtered.length === 0
  const showEmptyCategory = !isLoading && !hasSearch && filtered.length === 0
  const galleryCountLabel = t('gallery.count', { count: filtered.length })

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
          {isLoading ? (
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
            <ResourceGrid minColumnWidth={252}>
              {filtered.map((tpl) => (
                <TemplateCard
                  key={tpl.id}
                  template={tpl}
                  isCreating={creatingId === tpl.id}
                  creatingLocked={creatingId !== null}
                  onSelect={handleCreateFromTemplate}
                  ariaLabel={t('createFromTemplate')}
                  startLabel={t('startCta')}
                  toolsMoreFormatter={(count) => t('toolsMore', { count })}
                />
              ))}
            </ResourceGrid>
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
    <Link
      href="/agents/new"
      className={cn(
        'moldy-primary-action',
      )}
    >
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
        <div className="relative flex-1 sm:max-w-[360px]">
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
  const tools = template.recommended_tools ?? []
  const visibleTools = tools.slice(0, 2)
  const extraToolsCount = tools.length - visibleTools.length
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
        <span
          className={cn(
            'moldy-resource-icon',
            tone.icon,
          )}
        >
          <BotIcon aria-hidden className="size-4.5" />
        </span>
        <ResourceBadge tone={tone}>{template.category}</ResourceBadge>
      </ResourceListCard.Header>

      <ResourceListCard.Title>{template.name}</ResourceListCard.Title>
      <ResourceListCard.Description>{template.description ?? template.category}</ResourceListCard.Description>

      {tools.length > 0 && (
        <ResourceListCard.MetaRow>
          {visibleTools.map((tool) => (
            <ResourceCardMeta key={tool} className="max-w-[96px] gap-1">
              <WrenchIcon aria-hidden className="size-2.5 text-muted-foreground" />
              <span className="truncate leading-none">{tool}</span>
            </ResourceCardMeta>
          ))}
          {extraToolsCount > 0 && (
            <ResourceCardMeta>{toolsMoreFormatter(extraToolsCount)}</ResourceCardMeta>
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
        <div
          key={i}
          className="moldy-card flex min-h-[152px] flex-col p-4"
        >
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
