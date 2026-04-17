'use client'

import { useState } from 'react'
import { Loader2Icon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useUpdateMCPServer } from '@/lib/hooks/use-tools'
import type { MCPServerListItem } from '@/lib/types'

interface MCPServerRenameDialogProps {
  server: MCPServerListItem
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function MCPServerRenameDialog({ server, open, onOpenChange }: MCPServerRenameDialogProps) {
  const t = useTranslations('tool.mcpServer.rename')
  const tToast = useTranslations('tool.mcpServer.toast')
  const tc = useTranslations('common')
  const updateServer = useUpdateMCPServer()
  const [name, setName] = useState(server.name)

  function handleOpenChange(next: boolean) {
    if (next) setName(server.name)
    onOpenChange(next)
  }

  function handleSave() {
    const trimmed = name.trim()
    if (!trimmed || trimmed === server.name) {
      onOpenChange(false)
      return
    }
    updateServer.mutate(
      { id: server.id, data: { name: trimmed } },
      {
        onSuccess: () => onOpenChange(false),
        onError: () => toast.error(tToast('saveFailed')),
      },
    )
  }

  const disabled = updateServer.isPending || !name.trim()

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t('title')}</DialogTitle>
        </DialogHeader>

        <div className="space-y-2 py-2">
          <label className="text-sm font-medium" htmlFor="mcp-server-rename-input">
            {t('label')}
          </label>
          <Input
            id="mcp-server-rename-input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoFocus
          />
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {tc('cancel')}
          </Button>
          <Button onClick={handleSave} disabled={disabled}>
            {updateServer.isPending && (
              <Loader2Icon className="size-4 animate-spin" data-icon="inline-start" />
            )}
            {tc('save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
