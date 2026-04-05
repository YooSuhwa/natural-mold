'use client'

import { useState, useMemo } from 'react'
import { SearchIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import type { MiddlewareRegistryItem } from '@/lib/types'

type MiddlewareCategory = 'all' | 'context' | 'planning' | 'safety' | 'reliability' | 'provider'

interface AddMiddlewaresDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  allMiddlewares: MiddlewareRegistryItem[]
  selectedTypes: Set<string>
  onToggleMiddleware: (type: string) => void
}

export function AddMiddlewaresDialog({
  open,
  onOpenChange,
  allMiddlewares,
  selectedTypes,
  onToggleMiddleware,
}: AddMiddlewaresDialogProps) {
  const t = useTranslations('agent.visualSettings.addMiddlewaresDialog')
  const [search, setSearch] = useState('')
  const [checkedTypes, setCheckedTypes] = useState<Set<string>>(new Set())
  const [previewType, setPreviewType] = useState<string | null>(null)
  const [category, setCategory] = useState<MiddlewareCategory>('all')

  const categories: MiddlewareCategory[] = [
    'all',
    'context',
    'planning',
    'safety',
    'reliability',
    'provider',
  ]

  const filteredMiddlewares = useMemo(() => {
    let list = allMiddlewares
    if (category !== 'all') {
      list = list.filter((m) => m.category === category)
    }
    if (search.trim()) {
      const q = search.toLowerCase()
      list = list.filter(
        (m) => m.display_name.toLowerCase().includes(q) || m.description.toLowerCase().includes(q),
      )
    }
    return list
  }, [allMiddlewares, category, search])

  const previewMiddleware = allMiddlewares.find((m) => m.type === previewType) ?? null

  function handleToggleCheck(type: string) {
    setCheckedTypes((prev) => {
      const next = new Set(prev)
      if (next.has(type)) next.delete(type)
      else next.add(type)
      return next
    })
  }

  function handleAdd() {
    for (const type of checkedTypes) {
      if (!selectedTypes.has(type)) {
        onToggleMiddleware(type)
      }
    }
    handleClose()
  }

  function handleClose() {
    setSearch('')
    setCheckedTypes(new Set())
    setPreviewType(null)
    setCategory('all')
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent showCloseButton className="w-[900px] max-w-[900px] gap-0 p-0 sm:max-w-[900px]">
        <DialogHeader className="px-4 pt-4 pb-3">
          <DialogTitle>{t('title')}</DialogTitle>
          <DialogDescription>{t('description')}</DialogDescription>
        </DialogHeader>

        <div className="flex border-t border-border" style={{ height: '460px' }}>
          {/* Left panel */}
          <div className="flex w-1/3 flex-col border-r border-border">
            {/* Search */}
            <div className="relative px-3 py-2">
              <SearchIcon className="absolute top-1/2 left-5 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                placeholder={t('searchPlaceholder')}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="h-7 pl-7 text-xs"
              />
            </div>

            {/* Category filter */}
            <div className="flex flex-wrap gap-1 px-3 pb-2">
              {categories.map((cat) => (
                <button
                  key={cat}
                  onClick={() => setCategory(cat)}
                  className={`rounded-full px-2 py-0.5 text-[10px] font-medium transition-colors ${
                    category === cat
                      ? 'bg-primary text-primary-foreground'
                      : 'bg-muted text-muted-foreground hover:bg-muted/80'
                  }`}
                >
                  {t(`categories.${cat}`)}
                </button>
              ))}
            </div>

            {/* Middleware list */}
            <div className="flex-1 overflow-y-auto">
              {filteredMiddlewares.length === 0 ? (
                <div className="px-3 py-6 text-center text-xs text-muted-foreground">
                  {t('noResults')}
                </div>
              ) : (
                filteredMiddlewares.map((mw) => {
                  const isAlreadyAdded = selectedTypes.has(mw.type)
                  const isChecked = checkedTypes.has(mw.type)
                  return (
                    <div
                      key={mw.type}
                      className={`flex cursor-pointer items-start gap-2 px-3 py-2 hover:bg-muted/50 ${
                        previewType === mw.type ? 'bg-muted' : ''
                      }`}
                      onClick={() => setPreviewType(mw.type)}
                    >
                      <input
                        type="checkbox"
                        checked={isChecked || isAlreadyAdded}
                        disabled={isAlreadyAdded}
                        onChange={() => handleToggleCheck(mw.type)}
                        onClick={(e) => e.stopPropagation()}
                        className="mt-0.5 size-3.5 shrink-0 rounded border-input"
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5">
                          <span className="truncate text-xs font-medium">{mw.display_name}</span>
                          <Badge variant="outline" className="shrink-0 text-[8px] px-1 py-0">
                            {t(`categories.${mw.category}`)}
                          </Badge>
                        </div>
                        <div className="line-clamp-2 text-[10px] text-muted-foreground">
                          {mw.description}
                        </div>
                      </div>
                    </div>
                  )
                })
              )}
            </div>
          </div>

          {/* Right panel */}
          <div className="flex w-2/3 flex-col">
            {previewMiddleware ? (
              <div className="flex flex-1 flex-col overflow-y-auto p-4">
                <div className="flex items-center gap-2">
                  <h3 className="text-sm font-medium">{previewMiddleware.display_name}</h3>
                  <Badge variant="secondary">{previewMiddleware.name}</Badge>
                </div>
                <p className="mt-2 text-xs text-muted-foreground">
                  {previewMiddleware.description}
                </p>
                {previewMiddleware.provider_specific && (
                  <div className="mt-2">
                    <Badge variant="outline">
                      {t('providerOnly', { provider: previewMiddleware.provider_specific })}
                    </Badge>
                  </div>
                )}
                {Object.keys(previewMiddleware.config_schema).length > 0 && (
                  <div className="mt-3">
                    <span className="text-[10px] font-medium uppercase text-muted-foreground">
                      {t('configSchema')}
                    </span>
                    <pre className="mt-1 max-h-[200px] overflow-auto rounded-md bg-muted p-2 text-[10px]">
                      {JSON.stringify(previewMiddleware.config_schema, null, 2)}
                    </pre>
                  </div>
                )}
              </div>
            ) : (
              <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
                {t('selectToPreview')}
              </div>
            )}

            {/* Add button */}
            <div className="flex justify-end border-t border-border px-4 py-3">
              <Button size="sm" disabled={checkedTypes.size === 0} onClick={handleAdd}>
                {checkedTypes.size > 0 ? t('addCount', { count: checkedTypes.size }) : t('add')}
              </Button>
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
