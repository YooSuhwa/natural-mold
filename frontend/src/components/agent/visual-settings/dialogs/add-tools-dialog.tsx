'use client'

import { useState, useMemo } from 'react'
import { SearchIcon } from 'lucide-react'
import Link from 'next/link'
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
import type { Tool } from '@/lib/types'

interface AddToolsDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  allTools: Tool[]
  selectedToolIds: Set<string>
  onToggleTool: (toolId: string) => void
}

export function AddToolsDialog({
  open,
  onOpenChange,
  allTools,
  selectedToolIds,
  onToggleTool,
}: AddToolsDialogProps) {
  const t = useTranslations('agent.visualSettings.addToolsDialog')
  const [search, setSearch] = useState('')
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set())
  const [previewId, setPreviewId] = useState<string | null>(null)

  const filteredTools = useMemo(() => {
    if (!search.trim()) return allTools
    const q = search.toLowerCase()
    return allTools.filter(
      (tool) => tool.name.toLowerCase().includes(q) || tool.description?.toLowerCase().includes(q),
    )
  }, [allTools, search])

  const previewTool = allTools.find((tool) => tool.id === previewId) ?? null

  function handleToggleCheck(toolId: string) {
    setCheckedIds((prev) => {
      const next = new Set(prev)
      if (next.has(toolId)) next.delete(toolId)
      else next.add(toolId)
      return next
    })
  }

  function handleAdd() {
    for (const id of checkedIds) {
      if (!selectedToolIds.has(id)) {
        onToggleTool(id)
      }
    }
    handleClose()
  }

  function handleClose() {
    setSearch('')
    setCheckedIds(new Set())
    setPreviewId(null)
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

            {/* Tool list */}
            <div className="flex-1 overflow-y-auto">
              {filteredTools.length === 0 ? (
                <div className="px-3 py-6 text-center text-xs text-muted-foreground">
                  {t('noResults')}
                </div>
              ) : (
                filteredTools.map((tool) => {
                  const isAlreadyAdded = selectedToolIds.has(tool.id)
                  const isChecked = checkedIds.has(tool.id)
                  return (
                    <div
                      key={tool.id}
                      className={`flex cursor-pointer items-start gap-2 px-3 py-2 hover:bg-muted/50 ${
                        previewId === tool.id ? 'bg-muted' : ''
                      }`}
                      onClick={() => setPreviewId(tool.id)}
                    >
                      <input
                        type="checkbox"
                        checked={isChecked || isAlreadyAdded}
                        disabled={isAlreadyAdded}
                        onChange={() => handleToggleCheck(tool.id)}
                        onClick={(e) => e.stopPropagation()}
                        className="mt-0.5 size-3.5 shrink-0 rounded border-input"
                      />
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-xs font-medium">{tool.name}</div>
                        {tool.description && (
                          <div className="line-clamp-2 text-[10px] text-muted-foreground">
                            {tool.description}
                          </div>
                        )}
                      </div>
                    </div>
                  )
                })
              )}
            </div>

            {/* Footer link */}
            <div className="border-t border-border px-3 py-2">
              <Button variant="ghost" size="xs" render={<Link href="/tools" />}>
                {t('manageTools')}
              </Button>
            </div>
          </div>

          {/* Right panel */}
          <div className="flex w-2/3 flex-col">
            {previewTool ? (
              <div className="flex flex-1 flex-col overflow-y-auto p-4">
                <div className="flex items-center gap-2">
                  <h3 className="text-sm font-medium">{previewTool.name}</h3>
                  <Badge variant="secondary">{previewTool.type}</Badge>
                </div>
                {previewTool.description && (
                  <p className="mt-2 text-xs text-muted-foreground">{previewTool.description}</p>
                )}
                {previewTool.tags && previewTool.tags.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-1">
                    {previewTool.tags.map((tag) => (
                      <Badge key={tag} variant="outline">
                        {tag}
                      </Badge>
                    ))}
                  </div>
                )}
                {previewTool.parameters_schema && (
                  <div className="mt-3">
                    <span className="text-[10px] font-medium uppercase text-muted-foreground">
                      {t('parameters')}
                    </span>
                    <pre className="mt-1 max-h-[200px] overflow-auto rounded-md bg-muted p-2 text-[10px]">
                      {JSON.stringify(previewTool.parameters_schema, null, 2)}
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
              <Button size="sm" disabled={checkedIds.size === 0} onClick={handleAdd}>
                {checkedIds.size > 0 ? t('addCount', { count: checkedIds.size }) : t('add')}
              </Button>
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
