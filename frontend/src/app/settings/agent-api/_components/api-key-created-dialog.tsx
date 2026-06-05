'use client'

import { CopyIcon, KeyRoundIcon } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import type { AgentApiKeyCreated } from '@/lib/types'

interface ApiKeyCreatedDialogProps {
  createdKey: AgentApiKeyCreated | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function ApiKeyCreatedDialog({
  createdKey,
  open,
  onOpenChange,
}: ApiKeyCreatedDialogProps) {
  async function copyKey() {
    if (!createdKey?.key) return
    await navigator.clipboard.writeText(createdKey.key)
    toast.success('API key copied')
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <KeyRoundIcon className="size-4" />
            API key created
          </DialogTitle>
          <DialogDescription>
            This key is shown once. Store it securely on your server.
          </DialogDescription>
        </DialogHeader>

        <div className="moldy-muted-panel space-y-2 p-3">
          <p className="text-xs font-medium text-muted-foreground">Secret key</p>
          <div className="flex items-center gap-2">
            <code className="min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap text-xs">
              {createdKey?.key}
            </code>
            <Button variant="outline" size="icon-sm" onClick={copyKey} aria-label="Copy API key">
              <CopyIcon className="size-4" />
            </Button>
          </div>
        </div>

        <DialogFooter>
          <Button onClick={() => onOpenChange(false)}>Done</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
