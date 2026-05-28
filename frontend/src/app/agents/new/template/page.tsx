'use client'

import { useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  ArrowUpDownIcon,
  ChevronRightIcon,
  Loader2Icon,
  SearchIcon,
  SparklesIcon,
  WrenchIcon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'
import { useTemplates } from '@/lib/hooks/use-templates'
import { useCreateAgent } from '@/lib/hooks/use-agents'
import { useModels } from '@/lib/hooks/use-models'
import { EmptyState } from '@/components/shared/empty-state'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import type { Template } from '@/lib/types'

type SortKey = 'newest' | 'name'

const CATEGORIES: { value: string; labelKey: string }[] = [
  { value: '', labelKey: 'category.all' },
  { value: '생산성', labelKey: 'category.productivity' },
  { value: '커뮤니케이션', labelKey: 'category.communication' },
  { value: '데이터', labelKey: 'category.data' },
]

export default function TemplateSelectionPage() {
  const router = useRouter()
  const t = useTranslations('agent.template')
  const [selectedCategory, setSelectedCategory] = useState('')
  const [search, setSearch] = useState('')
  const [sortBy, setSortBy] = useState<SortKey>('newest')
  const [creatingId, setCreatingId] = useState<string | null>(null)

  const { data: templates, isLoading } = useTemplates(selectedCategory || undefined)
  const { data: models } = useModels()
  const createAgent = useCreateAgent()

  const defaultModelId = models?.find((m) => m.is_default)?.id ?? models?.[0]?.id ?? ''

  const filtered = useMemo(() => {
    if (!templates) return [] as Template[]
    const q = search.trim().toLowerCase()
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
  }, [templates, search, sortBy])

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

  return (
    <div className="flex flex-1 flex-col overflow-auto bg-gradient-to-b from-emerald-50/40 via-background to-background dark:from-emerald-950/15 dark:via-background dark:to-background">
      <div className="mx-auto flex w-full max-w-[1180px] flex-col gap-6 px-6 py-7 pb-20 md:px-8">
        <Hero title={t('pageTitle')} subtitle={t('subtitle')} />

        <FiltersBar
          selectedCategory={selectedCategory}
          onCategoryChange={setSelectedCategory}
          search={search}
          onSearchChange={setSearch}
          sortBy={sortBy}
          onSortToggle={() => setSortBy((s) => (s === 'newest' ? 'name' : 'newest'))}
          searchPlaceholder={t('search.placeholder')}
          searchAriaLabel={t('search.ariaLabel')}
          sortLabel={sortBy === 'newest' ? t('sort.newest') : t('sort.name')}
          sortAriaLabel={sortBy === 'newest' ? t('sort.ariaLabelNewest') : t('sort.ariaLabelName')}
          getCategoryLabel={(key) => t(key)}
        />

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
          <div
            className="grid gap-3.5"
            style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }}
          >
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
          </div>
        )}

        <BottomCta
          title={t('bottomCta.title')}
          subtitle={t('bottomCta.subtitle')}
          action={t('bottomCta.action')}
        />
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────── Hero

function Hero({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <header className="flex flex-col gap-1.5">
      <h1 className="text-[26px] font-bold leading-tight tracking-[-0.025em] text-foreground">
        {title}
      </h1>
      <p className="text-sm text-muted-foreground">{subtitle}</p>
    </header>
  )
}

// ───────────────────────────────────────────────────────── Filters bar

type FiltersBarProps = {
  selectedCategory: string
  onCategoryChange: (value: string) => void
  search: string
  onSearchChange: (value: string) => void
  sortBy: SortKey
  onSortToggle: () => void
  searchPlaceholder: string
  searchAriaLabel: string
  sortLabel: string
  sortAriaLabel: string
  getCategoryLabel: (key: string) => string
}

function FiltersBar({
  selectedCategory,
  onCategoryChange,
  search,
  onSearchChange,
  sortBy,
  onSortToggle,
  searchPlaceholder,
  searchAriaLabel,
  sortLabel,
  sortAriaLabel,
  getCategoryLabel,
}: FiltersBarProps) {
  return (
    <div className="flex flex-col gap-3.5">
      <div
        role="tablist"
        aria-label="템플릿 카테고리"
        className="inline-flex w-fit max-w-full gap-1 overflow-x-auto rounded-xl border border-border bg-muted/60 p-1"
      >
        {CATEGORIES.map((cat) => {
          const isActive = selectedCategory === cat.value
          return (
            <button
              key={cat.value || 'all'}
              type="button"
              role="tab"
              aria-selected={isActive}
              onClick={() => onCategoryChange(cat.value)}
              className={cn(
                'inline-flex h-8 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg px-3.5 text-sm transition-colors',
                isActive
                  ? 'bg-background font-semibold text-foreground shadow-sm'
                  : 'font-medium text-muted-foreground hover:text-foreground',
              )}
            >
              {getCategoryLabel(cat.labelKey)}
            </button>
          )
        })}
      </div>

      <div className="flex items-center gap-2.5">
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
      </div>
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

  const disabled = creatingLocked && !isCreating

  return (
    <button
      type="button"
      onClick={() => onSelect(template)}
      disabled={isCreating || disabled}
      aria-label={`${template.name} — ${ariaLabel}`}
      className={cn(
        'group relative flex flex-col rounded-xl border border-border bg-card p-5 text-left',
        'transition-all duration-150',
        'hover:-translate-y-px hover:border-emerald-200 hover:shadow-md',
        'dark:hover:border-emerald-500/30',
        'focus-visible:-translate-y-px focus-visible:border-emerald-300 focus-visible:shadow-md',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/40',
        'dark:focus-visible:border-emerald-500/40',
        isCreating && 'pointer-events-none opacity-70',
        disabled && 'pointer-events-none opacity-50',
      )}
    >
      <span className="text-sm font-semibold leading-tight tracking-[-0.015em] text-foreground">
        {template.name}
      </span>

      {template.description && (
        <p className="mt-2.5 line-clamp-2 min-h-[2.4em] text-xs leading-[1.55] text-muted-foreground">
          {template.description}
        </p>
      )}

      {tools.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          {visibleTools.map((tool) => (
            <span
              key={tool}
              className="inline-flex items-center gap-1 rounded border border-border bg-muted/60 px-1.5 py-0.5 text-[10.5px] font-medium text-foreground"
            >
              <WrenchIcon aria-hidden className="size-2.5 text-muted-foreground" />
              <span className="leading-none">{tool}</span>
            </span>
          ))}
          {extraToolsCount > 0 && (
            <span className="text-[10.5px] font-medium leading-none text-muted-foreground">
              {toolsMoreFormatter(extraToolsCount)}
            </span>
          )}
        </div>
      )}

      <div className="mt-3.5 flex items-center justify-end border-t border-dashed border-border pt-3">
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
    <div
      className="grid gap-3.5"
      style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }}
    >
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="flex flex-col rounded-xl border border-border bg-card p-5">
          <Skeleton className="h-4 w-32" />
          <Skeleton className="mt-3 h-3 w-full" />
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
    </div>
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
        'group mt-2 flex items-center gap-4 rounded-xl border border-emerald-100/60 p-5 transition-colors',
        'bg-gradient-to-r from-emerald-50 via-emerald-50/50 to-background',
        'hover:from-emerald-50 hover:to-emerald-50/30',
        'dark:border-emerald-500/15',
        'dark:from-emerald-950/30 dark:via-emerald-950/15 dark:to-background',
      )}
    >
      <span className="inline-flex size-10 shrink-0 items-center justify-center rounded-xl border border-border bg-card text-[var(--primary-strong)]">
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
