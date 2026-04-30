'use client'

import { useMemo, useState } from 'react'
import { LayersIcon, PlusIcon, SearchIcon, Trash2Icon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { useMiddlewares } from '@/lib/hooks/use-middlewares'
import type { MiddlewareRegistryItem } from '@/lib/types'

/**
 * 통합 미들웨어 추가 다이얼로그.
 *
 * SubAgents/ToolsSkills 다이얼로그와 같은 2-column (Current | Available) 패턴.
 * visual-settings의 MiddlewaresNode와 form-mode의 MiddlewaresBox 양쪽에서 사용.
 *
 * 카테고리 필터(chip)는 우측 컬럼 위에 노출.
 */

type CategoryFilter = 'all' | 'context' | 'planning' | 'safety' | 'reliability' | 'provider'

const CATEGORY_FILTERS: CategoryFilter[] = [
  'all',
  'context',
  'planning',
  'safety',
  'reliability',
  'provider',
]

interface MiddlewaresDialogProps {
  open: boolean
  onOpenChange: (v: boolean) => void
  selectedTypes: Set<string>
  onToggleMiddleware: (type: string) => void
  /**
   * 미리 로드된 카탈로그를 외부에서 주입할 수 있음 (visual-settings는 부모가 들고 있음).
   * 미제공 시 useMiddlewares()로 직접 조회.
   */
  allMiddlewares?: MiddlewareRegistryItem[]
}

export function MiddlewaresDialog({
  open,
  onOpenChange,
  selectedTypes,
  onToggleMiddleware,
  allMiddlewares: providedAll,
}: MiddlewaresDialogProps) {
  const tc = useTranslations('common')
  const { data: fetched } = useMiddlewares()
  const allMiddlewares = useMemo(
    () => providedAll ?? fetched ?? [],
    [providedAll, fetched],
  )
  const isLoading = !providedAll && !fetched

  const [query, setQuery] = useState('')
  const [category, setCategory] = useState<CategoryFilter>('all')

  const selected = useMemo(
    () => allMiddlewares.filter((m) => selectedTypes.has(m.type)),
    [allMiddlewares, selectedTypes],
  )
  const available = useMemo(() => {
    const q = query.trim().toLowerCase()
    return allMiddlewares
      .filter((m) => !selectedTypes.has(m.type))
      .filter((m) => (category === 'all' ? true : m.category === category))
      .filter((m) =>
        !q
          ? true
          : m.display_name.toLowerCase().includes(q) ||
            m.description.toLowerCase().includes(q) ||
            m.type.toLowerCase().includes(q),
      )
  }, [allMiddlewares, selectedTypes, category, query])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <LayersIcon className="size-5" />
            미들웨어 추가
          </DialogTitle>
          <DialogDescription>
            에이전트 호출에 끼워 넣을 미들웨어를 선택합니다.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-6 md:grid-cols-2">
          <CurrentColumn
            isLoading={isLoading}
            selected={selected}
            onRemove={onToggleMiddleware}
          />
          <AvailableColumn
            isLoading={isLoading}
            available={available}
            query={query}
            onQueryChange={setQuery}
            category={category}
            onCategoryChange={setCategory}
            onAdd={onToggleMiddleware}
            tc={tc}
          />
        </div>
      </DialogContent>
    </Dialog>
  )
}

function CurrentColumn({
  isLoading,
  selected,
  onRemove,
}: {
  isLoading: boolean
  selected: MiddlewareRegistryItem[]
  onRemove: (type: string) => void
}) {
  return (
    <section className="flex min-h-0 flex-col">
      <h3 className="mb-3 text-sm font-medium">현재 선택 ({selected.length})</h3>
      <div className="max-h-[60vh] space-y-2 overflow-y-auto pr-1 sm:h-[60vh]">
        {isLoading ? (
          <Skeleton className="h-20 w-full" />
        ) : selected.length === 0 ? (
          <EmptyBox>선택된 미들웨어가 없습니다.</EmptyBox>
        ) : (
          selected.map((m) => (
            <SelectedCard key={m.type} item={m} onRemove={() => onRemove(m.type)} />
          ))
        )}
      </div>
    </section>
  )
}

function AvailableColumn({
  isLoading,
  available,
  query,
  onQueryChange,
  category,
  onCategoryChange,
  onAdd,
  tc,
}: {
  isLoading: boolean
  available: MiddlewareRegistryItem[]
  query: string
  onQueryChange: (v: string) => void
  category: CategoryFilter
  onCategoryChange: (v: CategoryFilter) => void
  onAdd: (type: string) => void
  tc: (key: string) => string
}) {
  return (
    <section className="flex min-h-0 flex-col">
      <h3 className="mb-3 text-sm font-medium">추가 가능</h3>
      <div className="relative mb-3">
        <SearchIcon className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          placeholder="검색"
          className="pl-9 focus-visible:border-input focus-visible:ring-0"
        />
      </div>
      <div className="mb-3 flex flex-wrap gap-1">
        {CATEGORY_FILTERS.map((cat) => (
          <button
            key={cat}
            type="button"
            onClick={() => onCategoryChange(cat)}
            className={`rounded-full px-2.5 py-0.5 text-[11px] font-medium transition-colors ${
              category === cat
                ? 'bg-primary text-primary-foreground'
                : 'bg-muted text-muted-foreground hover:bg-muted/80'
            }`}
          >
            {cat === 'all' ? '전체' : cat}
          </button>
        ))}
      </div>
      <div className="max-h-[60vh] space-y-2 overflow-y-auto pr-1 sm:h-[60vh]">
        {isLoading ? (
          <Skeleton className="h-20 w-full" />
        ) : available.length === 0 ? (
          <EmptyBox>
            {query.trim().length > 0 ? '검색 결과가 없습니다.' : '표시할 미들웨어가 없습니다.'}
          </EmptyBox>
        ) : (
          available.map((m) => (
            <AvailableCard
              key={m.type}
              item={m}
              addLabel={tc('add')}
              onAdd={() => onAdd(m.type)}
            />
          ))
        )}
      </div>
    </section>
  )
}

function SelectedCard({
  item,
  onRemove,
}: {
  item: MiddlewareRegistryItem
  onRemove: () => void
}) {
  return (
    <MiddlewareCard
      item={item}
      align="center"
      subtitle={<p className="truncate font-mono text-[11px] text-muted-foreground">{item.type}</p>}
      categoryBadgeVariant="secondary"
      action={
        <Button
          size="sm"
          variant="ghost"
          onClick={onRemove}
          className="shrink-0"
          aria-label={`${item.display_name} 제거`}
        >
          <Trash2Icon className="size-3.5" />
          제거
        </Button>
      }
    />
  )
}

function AvailableCard({
  item,
  addLabel,
  onAdd,
}: {
  item: MiddlewareRegistryItem
  addLabel: string
  onAdd: () => void
}) {
  return (
    <MiddlewareCard
      item={item}
      align="start"
      subtitle={<p className="line-clamp-2 text-[11px] text-muted-foreground">{item.description}</p>}
      categoryBadgeVariant="outline"
      extraBadge={
        item.provider_specific ? (
          <Badge variant="secondary" className="shrink-0 text-[10px]">
            {item.provider_specific}
          </Badge>
        ) : null
      }
      action={
        <Button
          size="sm"
          variant="outline"
          onClick={onAdd}
          className="shrink-0"
          aria-label={`${item.display_name} 추가`}
        >
          <PlusIcon className="size-3.5" />
          {addLabel}
        </Button>
      }
    />
  )
}

function MiddlewareCard({
  item,
  align,
  subtitle,
  categoryBadgeVariant,
  extraBadge,
  action,
}: {
  item: MiddlewareRegistryItem
  align: 'start' | 'center'
  subtitle: React.ReactNode
  categoryBadgeVariant: 'secondary' | 'outline'
  extraBadge?: React.ReactNode
  action: React.ReactNode
}) {
  return (
    <div className={`flex gap-3 rounded-lg border p-3 ${align === 'center' ? 'items-center' : 'items-start'}`}>
      <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-amber-100 text-amber-600 dark:bg-amber-950/40 dark:text-amber-400">
        <LayersIcon className="size-4" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="truncate text-sm font-medium">{item.display_name}</p>
          <Badge variant={categoryBadgeVariant} className="shrink-0 text-[10px]">
            {item.category}
          </Badge>
          {extraBadge}
        </div>
        {subtitle}
      </div>
      {action}
    </div>
  )
}

function EmptyBox({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-32 items-center justify-center rounded-lg border border-dashed text-center text-sm text-muted-foreground">
      {children}
    </div>
  )
}
