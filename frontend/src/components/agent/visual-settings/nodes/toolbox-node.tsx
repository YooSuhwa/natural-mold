'use client'

import { useState } from 'react'
import { Handle, Position } from '@xyflow/react'
import { PlusIcon, TrashIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { AddToolsDialog } from '../dialogs/add-tools-dialog'
import type { Tool } from '@/lib/types'

export interface ToolboxNodeData {
  allTools: Tool[]
  selectedToolIds: Set<string>
  onToggleTool: (toolId: string) => void
  [key: string]: unknown
}

export function ToolboxNode({ data }: { data: ToolboxNodeData }) {
  const t = useTranslations('agent.visualSettings')
  const [dialogOpen, setDialogOpen] = useState(false)

  const { allTools = [], selectedToolIds, onToggleTool } = data
  const toolIds = selectedToolIds instanceof Set ? selectedToolIds : new Set<string>()
  const selectedTools = allTools.filter((tool) => toolIds.has(tool.id))

  return (
    <>
      <Handle type="target" position={Position.Left} className="!bg-indigo-500 !w-2.5 !h-2.5" />
      <div className="nowheel w-[220px] rounded-xl border bg-card shadow-md">
        {/* Header */}
        <div className="flex items-center justify-between border-b px-3 py-2">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            {t('nodes.toolbox')}
          </span>
          <div className="flex items-center gap-0.5">
            <Button variant="ghost" size="icon-xs" onClick={() => setDialogOpen(true)}>
              <PlusIcon className="size-3" />
            </Button>
            <Button variant="ghost" size="xs" disabled className="text-[10px] opacity-40">
              MCP
            </Button>
          </div>
        </div>

        {/* Content */}
        <div className="px-1 py-1">
          {selectedTools.length === 0 ? (
            <p className="px-2 py-2 text-xs text-muted-foreground">{t('toolbox.empty')}</p>
          ) : (
            <div className="max-h-[160px] overflow-y-auto">
              {selectedTools.map((tool) => (
                <div
                  key={tool.id}
                  className="group flex items-center justify-between rounded-md px-2 py-1 hover:bg-muted/50"
                >
                  <span className="truncate text-xs">{tool.name}</span>
                  <button
                    onClick={() => onToggleTool(tool.id)}
                    className="invisible shrink-0 p-0.5 text-muted-foreground hover:text-destructive group-hover:visible"
                    aria-label={t('toolbox.remove')}
                  >
                    <TrashIcon className="size-3" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <AddToolsDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        allTools={allTools}
        selectedToolIds={toolIds}
        onToggleTool={onToggleTool}
      />
    </>
  )
}
