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
  ResourceGrid,
  ResourcePage,
  ResourcePanel,
  ResourceToolbar,
} from '@/components/shared/resource-layout'
import { Skeleton } from '@/components/ui/skeleton'
import {
  getResourceTone,
  resourceCardClassName,
  resourceMetaClassName,
  type ResourceTone,
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
            <ResourceGrid>
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
        'inline-flex h-10 shrink-0 items-center justify-center gap-2 rounded-lg px-4 text-sm font-semibold text-white shadow-sm transition-colors',
        'bg-[var(--primary-strong)] hover:bg-[var(--primary-strong-hover)]',
        'focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
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
              'h-9 w-full rounded-lg border border-input bg-background pl-9 pr-3 text-sm text-foreground outline-hidden',
              'placeholder:text-muted-foreground',
              'transition-[border-color,box-shadow]',
              'focus:border-[var(--moldy-border-mint)] focus:shadow-[var(--moldy-shadow-focus)]',
              'dark:focus:border-emerald-500/40',
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
    <button
      type="button"
      onClick={() => onSelect(template)}
      disabled={isCreating || disabled}
      aria-label={`${template.name} — ${ariaLabel}`}
      className={cn(
        templateCardClassName(tone),
        isCreating && 'pointer-events-none opacity-70',
        disabled && 'pointer-events-none opacity-50',
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <span
          className={cn(
            'inline-flex size-9 shrink-0 items-center justify-center rounded-xl ring-1',
            tone.icon,
          )}
        >
          <BotIcon aria-hidden className="size-4.5" />
        </span>
        <span
          className={cn(
            'inline-flex min-w-0 max-w-[120px] items-center gap-1 rounded-md border px-2 py-1 moldy-ui-caption font-semibold leading-none',
            tone.badge,
          )}
        >
          <span className={cn('size-1.5 shrink-0 rounded-full', tone.dot)} />
          <span className="truncate">{template.category}</span>
        </span>
      </div>

      <span className="mt-3 line-clamp-1 moldy-ui-card-title font-bold leading-tight text-foreground">
        {template.name}
      </span>

      {template.description && (
        <p className="mt-2 line-clamp-2 min-h-[2.65em] text-xs leading-[1.45] text-muted-foreground">
          {template.description}
        </p>
      )}

      {tools.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          {visibleTools.map((tool) => (
            <span
              key={tool}
              className={cn(resourceMetaClassName, 'max-w-[96px] gap-1')}
            >
              <WrenchIcon aria-hidden className="size-2.5 text-muted-foreground" />
              <span className="truncate leading-none">{tool}</span>
            </span>
          ))}
          {extraToolsCount > 0 && (
            <span className="moldy-ui-meta font-medium leading-none text-muted-foreground">
              {toolsMoreFormatter(extraToolsCount)}
            </span>
          )}
        </div>
      )}

      <div className="mt-auto flex items-center justify-end pt-3">
        {isCreating ? (
          <Loader2Icon className="size-3.5 animate-spin text-muted-foreground" />
        ) : (
          <span
            className={cn(
              'inline-flex items-center gap-0.5 text-xs font-semibold text-muted-foreground transition-[color,transform] duration-150',
              'group-hover:translate-x-0.5 group-hover:text-primary-strong',
              'group-focus-visible:translate-x-0.5 group-focus-visible:text-primary-strong',
            )}
          >
            {startLabel}
            <ChevronRightIcon aria-hidden className="size-3" />
          </span>
        )}
      </div>
    </button>
  )
}

// ───────────────────────────────────────────────────────── No search results

function NoSearchResults({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-border bg-card/50 px-6 py-12 text-center">
      <SearchIcon aria-hidden className="size-7 text-muted-foreground/50" />
      <div className="text-sm font-semibold text-foreground">{title}</div>
      <div className="text-xs text-muted-foreground">{subtitle}</div>
    </div>
  )
}

// ───────────────────────────────────────────────────────── Grid skeleton

function TemplateGridSkeleton() {
  return (
    <ResourceGrid>
      {Array.from({ length: 6 }).map((_, i) => (
        <div
          key={i}
          className="flex min-h-[152px] flex-col rounded-xl border border-border bg-card p-4"
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
        'group mt-auto flex shrink-0 items-center gap-4 rounded-lg border border-emerald-100/60 p-4 transition-colors',
        'bg-gradient-to-r from-emerald-50 via-emerald-50/50 to-background',
        'hover:from-emerald-50 hover:to-emerald-50/30',
        'dark:border-emerald-500/15',
        'dark:from-emerald-950/30 dark:via-emerald-950/15 dark:to-background',
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

function templateCardClassName(tone: ResourceTone): string {
  return resourceCardClassName(tone, 'min-h-[152px]')
}
