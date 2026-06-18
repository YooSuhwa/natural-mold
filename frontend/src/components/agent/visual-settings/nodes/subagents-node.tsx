'use client'

import { useState } from 'react'
import { Handle, Position } from '@xyflow/react'
import { PlusIcon, TrashIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import { SubAgentsDialog } from '@/components/agent/sub-agents-dialog'
import { useAgents } from '@/lib/hooks/use-agents'

export interface SubagentsNodeData {
  selectedSubAgentIds: Set<string>
  onToggleSubAgent: (id: string) => void
  /** 자기 자신 제외용. 매뉴얼(create) 페이지에선 빈 문자열. */
  currentAgentId: string
  [key: string]: unknown
}

export function SubagentsNode({ data }: { data: SubagentsNodeData }) {
  const t = useTranslations('agent.visualSettings')
  const [dialogOpen, setDialogOpen] = useState(false)
  const { data: agents } = useAgents()

  const { selectedSubAgentIds, onToggleSubAgent, currentAgentId = '' } = data
  const ids = selectedSubAgentIds instanceof Set ? selectedSubAgentIds : new Set<string>()
  const selected = (agents ?? []).filter((a) => ids.has(a.id))

  return (
    <>
      <Handle
        type="target"
        position={Position.Left}
        className="moldy-flow-handle moldy-flow-handle-subagents"
      />
      <div className="moldy-flow-node nowheel w-56">
        <div className="flex items-center justify-between border-b px-3 py-2">
          <span className="moldy-ui-micro font-semibold uppercase tracking-wider text-muted-foreground">
            {t('nodes.subagents')}
          </span>
          <Button variant="ghost" size="icon-xs" onClick={() => setDialogOpen(true)}>
            <PlusIcon className="size-3" />
          </Button>
        </div>

        <div className="px-1 py-1">
          {selected.length === 0 ? (
            <p className="px-2 py-2 text-xs text-muted-foreground">{t('subagents.empty')}</p>
          ) : (
            <div className="max-h-40 overflow-y-auto">
              {selected.map((agent) => (
                <div
                  key={agent.id}
                  className="group flex items-center justify-between rounded-md px-2 py-1 hover:bg-muted/50"
                >
                  <span className="truncate text-xs">{agent.name}</span>
                  <button
                    onClick={() => onToggleSubAgent(agent.id)}
                    className="invisible shrink-0 p-0.5 text-muted-foreground hover:text-destructive group-hover:visible"
                    aria-label={t('subagents.remove')}
                  >
                    <TrashIcon className="size-3" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <SubAgentsDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        selectedSubAgentIds={ids}
        onToggleSubAgent={onToggleSubAgent}
        currentAgentId={currentAgentId}
      />
    </>
  )
}
