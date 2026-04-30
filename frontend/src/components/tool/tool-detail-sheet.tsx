'use client'

import { useState } from 'react'
import { toast } from 'sonner'
import { Trash2 } from 'lucide-react'

import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { Badge } from '@/components/ui/badge'
import { DomainIcon } from '@/components/shared/icon'
import { CredentialPicker } from '@/components/credential/credential-picker'
import { ToolRunPanel } from './tool-run-panel'
import { useTool, useUpdateTool, useDeleteTool, useToolType } from '@/lib/hooks/use-tools'

interface ToolDetailSheetProps {
  toolId: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function ToolDetailSheet({ toolId, open, onOpenChange }: ToolDetailSheetProps) {
  const { data: tool } = useTool(toolId)
  const { data: definition } = useToolType(tool?.definition_key)
  const update = useUpdateTool()
  const remove = useDeleteTool()
  const [confirming, setConfirming] = useState(false)

  async function handleCredentialChange(next: string | null) {
    if (!tool) return
    try {
      await update.mutateAsync({ id: tool.id, data: { credential_id: next } })
      toast.success('Credential updated')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Update failed')
    }
  }

  async function handleDelete() {
    if (!tool) return
    try {
      await remove.mutateAsync(tool.id)
      toast.success('Tool deleted')
      onOpenChange(false)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Delete failed')
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full sm:max-w-md flex flex-col gap-4 overflow-y-auto p-0">
        <SheetHeader className="border-b">
          {tool ? (
            <>
              <div className="flex items-center gap-2">
                <DomainIcon iconId={definition?.icon_id ?? tool.definition_key} />
                <SheetTitle>{tool.name}</SheetTitle>
              </div>
              <SheetDescription>
                <Badge variant="secondary" className="mr-1">
                  {tool.definition_key}
                </Badge>
                {tool.description}
              </SheetDescription>
            </>
          ) : (
            <SheetTitle>Loading...</SheetTitle>
          )}
        </SheetHeader>

        {tool && definition && (
          <div className="flex-1 px-4 pb-4 space-y-4">
            {definition.credential_definition_keys.length > 0 && (
              <div className="space-y-1.5">
                <label className="text-xs font-medium">Credential</label>
                <CredentialPicker
                  value={tool.credential_id}
                  onChange={handleCredentialChange}
                  definitionKeys={definition.credential_definition_keys}
                />
              </div>
            )}

            <Separator />

            <ToolRunPanel toolId={tool.id} />

            <Separator />

            <Button
              variant="outline"
              size="sm"
              className="text-destructive hover:text-destructive"
              onClick={() => setConfirming(true)}
            >
              <Trash2 className="size-3.5" />
              Delete tool
            </Button>

            {confirming && (
              <div className="rounded border border-destructive/40 bg-destructive/5 p-3 text-xs">
                <p className="font-medium text-destructive">Delete this tool?</p>
                <div className="mt-2 flex gap-2">
                  <Button size="sm" variant="outline" onClick={() => setConfirming(false)}>
                    Cancel
                  </Button>
                  <Button
                    size="sm"
                    variant="destructive"
                    onClick={handleDelete}
                    disabled={remove.isPending}
                  >
                    Confirm delete
                  </Button>
                </div>
              </div>
            )}
          </div>
        )}
      </SheetContent>
    </Sheet>
  )
}
