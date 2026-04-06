'use client'

import { useState, useMemo } from 'react'
import { Handle, Position } from '@xyflow/react'
import type { NodeProps } from '@xyflow/react'
import { PencilIcon, CheckIcon, ChevronDownIcon, ExternalLinkIcon, SearchIcon } from 'lucide-react'
import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Dialog, DialogTrigger, DialogContent } from '@/components/ui/dialog'
import { getProviderIcon, formatContextWindow } from '@/lib/utils/provider'
import type { Model } from '@/lib/types'

interface AgentNodeData {
  name: string
  description: string
  modelId: string
  modelName: string
  systemPrompt: string
  temperature: number
  topP: number
  maxTokens: number
  models: Model[]
  onUpdate: (data: {
    name: string
    description: string
    modelId: string
    systemPrompt: string
    temperature: number
    topP: number
    maxTokens: number
  }) => void
  [key: string]: unknown
}

export function AgentNode({ data }: NodeProps & { data: AgentNodeData }) {
  const t = useTranslations('agent.visualSettings')
  const ts = useTranslations('agent.settings')

  const [dialogOpen, setDialogOpen] = useState(false)
  const [editName, setEditName] = useState(data.name)
  const [editDesc, setEditDesc] = useState(data.description)
  const [editModelId, setEditModelId] = useState(data.modelId)
  const [editPrompt, setEditPrompt] = useState(data.systemPrompt)
  const [editTemp, setEditTemp] = useState(data.temperature)
  const [editTopP, setEditTopP] = useState(data.topP)
  const [editMaxTokens, setEditMaxTokens] = useState(data.maxTokens)
  const [modelSearch, setModelSearch] = useState('')

  const filteredModels = useMemo(() => {
    const q = modelSearch.toLowerCase()
    return q ? data.models.filter((m) => m.display_name.toLowerCase().includes(q)) : data.models
  }, [data.models, modelSearch])

  function handleOpenChange(open: boolean) {
    if (open) {
      setEditName(data.name)
      setEditDesc(data.description)
      setEditModelId(data.modelId)
      setEditPrompt(data.systemPrompt)
      setEditTemp(data.temperature)
      setEditTopP(data.topP)
      setEditMaxTokens(data.maxTokens)
    }
    setDialogOpen(open)
    if (open) setModelSearch('')
  }

  function handleDone() {
    data.onUpdate({
      name: editName,
      description: editDesc,
      modelId: editModelId,
      systemPrompt: editPrompt,
      temperature: editTemp,
      topP: editTopP,
      maxTokens: editMaxTokens,
    })
    setDialogOpen(false)
  }

  return (
    <>
      <Handle type="target" position={Position.Left} className="!bg-amber-500 !w-2.5 !h-2.5" />
      <div className="nowheel w-[280px] rounded-xl border bg-card shadow-md">
        <div className="flex items-center justify-between border-b px-3 py-2">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            {t('nodes.agent')}
          </span>
          <Dialog open={dialogOpen} onOpenChange={handleOpenChange}>
            <DialogTrigger
              render={
                <Button variant="ghost" size="icon-sm">
                  <PencilIcon className="size-3.5" />
                </Button>
              }
            />
            <DialogContent className="w-[900px] max-w-[900px] sm:max-w-[900px] gap-0 p-0 overflow-hidden">
              <div className="min-h-14 p-4 pr-12">
                <Input
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  placeholder={ts('name')}
                  className="border-transparent bg-transparent px-0 text-base font-semibold hover:border-border focus:border-primary h-auto"
                />
                <Input
                  value={editDesc}
                  onChange={(e) => setEditDesc(e.target.value)}
                  placeholder={ts('descriptionPlaceholder')}
                  className="border-transparent bg-transparent px-0 text-xs text-muted-foreground hover:border-border focus:border-primary h-auto mt-1"
                />
              </div>

              <div className="flex h-[600px] border-t border-border">
                <div className="flex w-1/3 min-w-0 flex-col border-r border-border">
                  <div className="flex items-center gap-2 border-b border-border px-3 py-2">
                    <SearchIcon className="size-4 shrink-0 text-muted-foreground" />
                    <input
                      className="h-7 flex-1 border-0 bg-transparent text-sm shadow-none outline-none ring-0 placeholder:text-muted-foreground focus:outline-none focus:ring-0"
                      placeholder={t('editDialog.searchModels')}
                      value={modelSearch}
                      onChange={(e) => setModelSearch(e.target.value)}
                    />
                  </div>
                  <div className="flex-1 overflow-auto p-1.5">
                    {filteredModels.map((model) => (
                      <button
                        key={model.id}
                        type="button"
                        onClick={() => setEditModelId(model.id)}
                        className={`flex w-full items-center gap-2.5 rounded-md px-2.5 py-2.5 text-left text-sm ${
                          editModelId === model.id ? 'bg-muted/60' : 'hover:bg-muted/30'
                        }`}
                      >
                        {editModelId === model.id ? (
                          <CheckIcon className="size-3.5 shrink-0 text-primary" />
                        ) : (
                          <span className="size-3.5 shrink-0" />
                        )}
                        <div className="flex size-5 items-center justify-center rounded bg-muted text-[8px] font-bold text-muted-foreground">
                          {getProviderIcon(model.provider)}
                        </div>
                        <div className="flex min-w-0 flex-1 items-center gap-1.5">
                          <span className="text-xs font-medium truncate">{model.display_name}</span>
                          {model.context_window && (
                            <Badge variant="outline" className="shrink-0 text-[8px] px-1 py-0">
                              {formatContextWindow(model.context_window)}
                            </Badge>
                          )}
                        </div>
                      </button>
                    ))}
                  </div>

                  {/* Model params — collapsible */}
                  <details className="group border-t border-border">
                    <summary className="flex cursor-pointer items-center justify-between px-3 py-2.5 text-xs font-medium text-muted-foreground select-none hover:bg-muted/30">
                      {ts('modelParams')}
                      <ChevronDownIcon className="size-3.5 rotate-180 transition-transform group-open:rotate-0" />
                    </summary>
                    <div className="space-y-3 border-t border-border px-3 py-3">
                      <div className="space-y-1">
                        <div className="flex items-center justify-between text-xs">
                          <span className="text-muted-foreground">Temperature</span>
                          <span className="font-mono text-[10px] tabular-nums">
                            {editTemp.toFixed(1)}
                          </span>
                        </div>
                        <input
                          type="range"
                          min="0"
                          max="2"
                          step="0.1"
                          value={editTemp}
                          onChange={(e) => setEditTemp(Number(e.target.value))}
                          className="w-full accent-primary"
                        />
                      </div>
                      <div className="space-y-1">
                        <div className="flex items-center justify-between text-xs">
                          <span className="text-muted-foreground">Top P</span>
                          <span className="font-mono text-[10px] tabular-nums">
                            {editTopP.toFixed(1)}
                          </span>
                        </div>
                        <input
                          type="range"
                          min="0"
                          max="1"
                          step="0.1"
                          value={editTopP}
                          onChange={(e) => setEditTopP(Number(e.target.value))}
                          className="w-full accent-primary"
                        />
                      </div>
                      <div className="space-y-1">
                        <div className="flex items-center justify-between text-xs">
                          <span className="text-muted-foreground">Max Tokens</span>
                        </div>
                        <Input
                          type="number"
                          min="256"
                          max="32768"
                          step="256"
                          value={editMaxTokens}
                          onChange={(e) => setEditMaxTokens(Number(e.target.value) || 4096)}
                          className="h-7 text-xs"
                        />
                      </div>
                    </div>
                  </details>

                  {/* Manage models link */}
                  <div className="border-t border-border px-3 py-2">
                    <Link
                      href="/models"
                      className="flex w-full items-center justify-center gap-1.5 rounded-md border px-2 py-1.5 text-xs text-muted-foreground hover:bg-muted/50 transition-colors"
                    >
                      {ts('manageModels')}
                      <ExternalLinkIcon className="size-3.5" />
                    </Link>
                  </div>
                </div>

                <div className="flex w-2/3 min-w-0 flex-col">
                  <div className="relative flex min-h-0 flex-1 flex-col">
                    <div className="min-h-0 flex-1 overflow-auto px-4 py-3">
                      <Textarea
                        value={editPrompt}
                        onChange={(e) => setEditPrompt(e.target.value)}
                        placeholder={ts('systemPrompt')}
                        className="min-h-full resize-none border-0 p-0 font-mono text-xs shadow-none focus-visible:ring-0"
                      />
                    </div>
                    <div className="absolute bottom-3 right-4">
                      <Button size="sm" onClick={handleDone}>
                        <CheckIcon className="size-3.5" />
                        {t('editDialog.done')}
                      </Button>
                    </div>
                  </div>
                </div>
              </div>
            </DialogContent>
          </Dialog>
        </div>
        <div className="space-y-1.5 px-3 py-2.5">
          <p className="text-sm font-semibold truncate">{data.name}</p>
          {data.description && (
            <p className="text-xs text-muted-foreground truncate">{data.description}</p>
          )}
          <p className="text-xs text-muted-foreground">{data.modelName}</p>
          <p className="line-clamp-4 text-[11px] leading-relaxed text-muted-foreground/80 whitespace-pre-wrap">
            {data.systemPrompt}
          </p>
        </div>
      </div>
      <Handle type="source" position={Position.Right} className="!bg-indigo-500 !w-2.5 !h-2.5" />
    </>
  )
}
