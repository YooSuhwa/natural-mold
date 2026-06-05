'use client'

import { useMemo, useState } from 'react'
import { KeyRoundIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import type {
  AgentApiKeyCreateRequest,
  AgentApiKeyCreated,
  AgentApiScope,
  AgentDeployment,
} from '@/lib/types'

const ALL_SCOPES: AgentApiScope[] = ['invoke', 'stream', 'background', 'read']

interface ApiKeyCreateDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  deployments: AgentDeployment[]
  onCreate: (data: AgentApiKeyCreateRequest) => Promise<AgentApiKeyCreated>
  onCreated: (key: AgentApiKeyCreated) => void
}

export function ApiKeyCreateDialog({
  open,
  onOpenChange,
  deployments,
  onCreate,
  onCreated,
}: ApiKeyCreateDialogProps) {
  const [name, setName] = useState('Production key')
  const [expiresInDays, setExpiresInDays] = useState('')
  const [scopes, setScopes] = useState<Set<AgentApiScope>>(new Set(['invoke', 'stream']))
  const [allowAll, setAllowAll] = useState(false)
  const [selectedDeployments, setSelectedDeployments] = useState<Set<string>>(new Set())
  const [submitting, setSubmitting] = useState(false)

  const canSubmit = useMemo(() => {
    return name.trim().length > 0 && (allowAll || selectedDeployments.size > 0) && scopes.size > 0
  }, [allowAll, name, scopes.size, selectedDeployments.size])

  function toggleScope(scope: AgentApiScope, checked: boolean) {
    setScopes((prev) => {
      const next = new Set(prev)
      if (checked) next.add(scope)
      else next.delete(scope)
      return next
    })
  }

  function toggleDeployment(id: string, checked: boolean) {
    setSelectedDeployments((prev) => {
      const next = new Set(prev)
      if (checked) next.add(id)
      else next.delete(id)
      return next
    })
  }

  async function submit() {
    if (!canSubmit) return
    setSubmitting(true)
    try {
      const created = await onCreate({
        name: name.trim(),
        scopes: Array.from(scopes),
        allow_all_deployments: allowAll,
        deployment_ids: allowAll ? [] : Array.from(selectedDeployments),
        expires_in_days: expiresInDays ? Number(expiresInDays) : null,
      })
      onOpenChange(false)
      onCreated(created)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <KeyRoundIcon className="size-4" />
            Create API key
          </DialogTitle>
          <DialogDescription>
            Generate a server-side key and limit which deployed agents it can call.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <label className="block space-y-1.5">
            <span className="text-xs font-medium text-muted-foreground">Name</span>
            <Input value={name} onChange={(event) => setName(event.target.value)} />
          </label>

          <label className="block space-y-1.5">
            <span className="text-xs font-medium text-muted-foreground">Expires in days</span>
            <Input
              type="number"
              min={1}
              max={365}
              placeholder="No expiry"
              value={expiresInDays}
              onChange={(event) => setExpiresInDays(event.target.value)}
            />
          </label>

          <div className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground">Scopes</p>
            <div className="grid gap-2 sm:grid-cols-2">
              {ALL_SCOPES.map((scope) => (
                <label key={scope} className="moldy-muted-panel flex items-center gap-2 p-3">
                  <Checkbox
                    checked={scopes.has(scope)}
                    onCheckedChange={(value) => toggleScope(scope, Boolean(value))}
                  />
                  <span className="text-sm font-medium">{scope}</span>
                </label>
              ))}
            </div>
          </div>

          <div className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground">Agent access</p>
            <label className="moldy-muted-panel flex items-center gap-2 p-3">
              <Checkbox checked={allowAll} onCheckedChange={(value) => setAllowAll(Boolean(value))} />
              <span className="text-sm font-medium">All deployed agents</span>
            </label>
            {!allowAll && (
              <div className="max-h-48 space-y-2 overflow-auto">
                {deployments.map((deployment) => (
                  <label key={deployment.id} className="moldy-muted-panel flex items-center gap-2 p-3">
                    <Checkbox
                      checked={selectedDeployments.has(deployment.id)}
                      onCheckedChange={(value) => toggleDeployment(deployment.id, Boolean(value))}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">{deployment.agent_name}</span>
                      <span className="block truncate font-mono text-xs text-muted-foreground">
                        {deployment.public_id}
                      </span>
                    </span>
                  </label>
                ))}
                {deployments.length === 0 && (
                  <div className="moldy-muted-panel p-3 text-sm text-muted-foreground">
                    Deploy an agent before creating a scoped key.
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={!canSubmit || submitting}>
            Create
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
