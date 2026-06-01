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
import { cn } from '@/lib/utils'
import type { Template } from '@/lib/types'

type SortKey = 'newest' | 'name'
type CardTone = {
  card: string
  icon: string
  badge: string
  dot: string
}

const CATEGORIES: { value: string; labelKey: string }[] = [
  { value: '', labelKey: 'category.all' },
  { value: 'category.productivityValue', labelKey: 'category.productivity' },
  { value: 'category.communicationValue', labelKey: 'category.communication' },
  { value: 'category.dataValue', labelKey: 'category.data' },
]

const CARD_TONES: CardTone[] = [
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
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
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
              'h-9 w-full rounded-lg border border-input bg-background pl-9 pr-3 text-sm text-foreground outline-none',
              'placeholder:text-muted-foreground',
              'transition-[border-color,box-shadow]',
              'focus:border-emerald-300 focus:shadow-[0_0_0_3px_oklch(0.596_0.145_163.225/0.12)]',
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
  const tone = pickCardTone(template)

  const disabled = creatingLocked && !isCreating

  return (
    <button
      type="button"
      onClick={() => onSelect(template)}
      disabled={isCreating || disabled}
      aria-label={`${template.name} — ${ariaLabel}`}
      className={cn(
        'group relative flex min-h-[152px] flex-col rounded-md border border-transparent p-4 text-left',
        'shadow-[0_10px_24px_-22px_rgba(15,23,42,0.45)] transition-all duration-150',
        'hover:-translate-y-px hover:shadow-[0_18px_32px_-24px_rgba(15,23,42,0.55)]',
        'focus-visible:-translate-y-px focus-visible:border-emerald-300 focus-visible:shadow-md',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/40',
        'dark:focus-visible:border-emerald-500/40',
        tone.card,
        isCreating && 'pointer-events-none opacity-70',
        disabled && 'pointer-events-none opacity-50',
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <span
          className={cn(
            'inline-flex size-9 shrink-0 items-center justify-center rounded-lg',
            tone.icon,
          )}
        >
          <BotIcon aria-hidden className="size-4.5" />
        </span>
        <span
          className={cn(
            'inline-flex min-w-0 max-w-[120px] items-center gap-1 rounded-md border px-2 py-1 text-[11px] font-semibold leading-none',
            tone.badge,
          )}
        >
          <span className={cn('size-1.5 shrink-0 rounded-full', tone.dot)} />
          <span className="truncate">{template.category}</span>
        </span>
      </div>

      <span className="mt-3 line-clamp-1 text-[15px] font-bold leading-tight text-foreground">
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
              className="inline-flex max-w-[96px] items-center gap-1 rounded border border-white/80 bg-white/55 px-1.5 py-0.5 text-[10.5px] font-semibold text-foreground shadow-sm dark:border-white/10 dark:bg-white/10"
            >
              <WrenchIcon aria-hidden className="size-2.5 text-muted-foreground" />
              <span className="truncate leading-none">{tool}</span>
            </span>
          ))}
          {extraToolsCount > 0 && (
            <span className="text-[10.5px] font-medium leading-none text-muted-foreground">
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
              'inline-flex items-center gap-0.5 text-xs font-semibold text-muted-foreground transition-all duration-150',
              'group-hover:translate-x-0.5 group-hover:text-[var(--primary-strong)]',
              'group-focus-visible:translate-x-0.5 group-focus-visible:text-[var(--primary-strong)]',
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
          className="flex min-h-[152px] flex-col rounded-md border border-border bg-card p-4"
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
      <span className="inline-flex size-10 shrink-0 items-center justify-center rounded-lg border border-border bg-card text-[var(--primary-strong)]">
        <SparklesIcon className="size-5" />
      </span>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-semibold tracking-[-0.015em] text-foreground">{title}</div>
        <div className="mt-0.5 text-xs text-muted-foreground">{subtitle}</div>
      </div>
      <span className="inline-flex shrink-0 items-center gap-0.5 text-sm font-semibold text-[var(--primary-strong)] transition-transform group-hover:translate-x-0.5">
        {action}
        <ChevronRightIcon aria-hidden className="size-3.5" />
      </span>
    </Link>
  )
}

function pickCardTone(template: Template): CardTone {
  const seed = `${template.category}:${template.name}`
  let hash = 0
  for (let i = 0; i < seed.length; i += 1) hash += seed.charCodeAt(i)
  return CARD_TONES[hash % CARD_TONES.length]
}
