'use client'

import { useState } from 'react'
import { Handle, Position } from '@xyflow/react'
import { PlusIcon, TrashIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { AddMiddlewaresDialog } from '../dialogs/add-middlewares-dialog'
import type { MiddlewareRegistryItem } from '@/lib/types'

export interface MiddlewaresNodeData {
  allMiddlewares: MiddlewareRegistryItem[]
  selectedTypes: Set<string>
  onToggleMiddleware: (type: string) => void
  [key: string]: unknown
}

export function MiddlewaresNode({ data }: { data: MiddlewaresNodeData }) {
  const t = useTranslations('agent.visualSettings')
  const [dialogOpen, setDialogOpen] = useState(false)

  const { allMiddlewares = [], selectedTypes, onToggleMiddleware } = data
  const types = selectedTypes instanceof Set ? selectedTypes : new Set<string>()
  const selectedMiddlewares = allMiddlewares.filter((mw) => types.has(mw.type))

  return (
    <>
      <Handle type="target" position={Position.Left} className="!bg-amber-500 !w-2.5 !h-2.5" />
      <div className="nowheel w-[220px] rounded-xl border bg-card shadow-md">
        {/* Header */}
        <div className="flex items-center justify-between border-b px-3 py-2">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            {t('nodes.middlewares')}
          </span>
          <div className="flex items-center gap-0.5">
            <Button variant="ghost" size="icon-xs" onClick={() => setDialogOpen(true)}>
              <PlusIcon className="size-3" />
            </Button>
          </div>
        </div>

        {/* Content */}
        <div className="px-1 py-1">
          {selectedMiddlewares.length === 0 ? (
            <p className="px-2 py-2 text-xs text-muted-foreground">{t('middlewares.empty')}</p>
          ) : (
            <div className="max-h-[160px] overflow-y-auto">
              {selectedMiddlewares.map((mw) => (
                <div
                  key={mw.type}
                  className="group flex items-center justify-between rounded-md px-2 py-1 hover:bg-muted/50"
                >
                  <span className="truncate text-xs">{mw.display_name}</span>
                  <button
                    onClick={() => onToggleMiddleware(mw.type)}
                    className="invisible shrink-0 p-0.5 text-muted-foreground hover:text-destructive group-hover:visible"
                    aria-label={t('middlewares.remove')}
                  >
                    <TrashIcon className="size-3" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <AddMiddlewaresDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        allMiddlewares={allMiddlewares}
        selectedTypes={types}
        onToggleMiddleware={onToggleMiddleware}
      />
    </>
  )
}
