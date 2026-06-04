'use client'

import { CopyIcon, KeyRoundIcon, Loader2Icon, RocketIcon } from 'lucide-react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { API_BASE } from '@/lib/api/client'
import {
  useAgentApiKeys,
  useAgentDeploymentCandidates,
  useAgentDeployments,
  useCreateAgentDeployment,
} from '@/lib/hooks/use-agent-api'

interface ApiPanelProps {
  agentId: string
  agentName: string
}

export function ApiPanel({ agentId, agentName }: ApiPanelProps) {
  const candidates = useAgentDeploymentCandidates()
  const deployments = useAgentDeployments()
  const apiKeys = useAgentApiKeys()
  const createDeployment = useCreateAgentDeployment()

  const deployment = (deployments.data ?? []).find((item) => item.agent_id === agentId)
  const candidate = (candidates.data ?? []).find((item) => item.agent_id === agentId)
  const relatedKeys = (apiKeys.data ?? []).filter(
    (key) =>
      key.allow_all_deployments ||
      key.deployments.some((item) => item.deployment_id === deployment?.id),
  )

  async function copy(value: string) {
    await navigator.clipboard.writeText(value)
    toast.success('Copied')
  }

  async function deploy() {
    await createDeployment.mutateAsync({ agent_id: agentId })
    toast.success('Agent deployed')
  }

  if (deployments.isLoading || candidates.isLoading) {
    return (
      <div className="moldy-muted-panel flex items-center justify-center p-8">
        <Loader2Icon className="size-5 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="moldy-card space-y-3 p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 space-y-1">
            <h3 className="truncate text-sm font-semibold text-foreground">{agentName}</h3>
            <p className="text-xs text-muted-foreground">External Agent API deployment</p>
          </div>
          {deployment ? <Badge variant="secondary">deployed</Badge> : <Badge variant="outline">not deployed</Badge>}
        </div>

        {deployment ? (
          <div className="space-y-2">
            <InfoRow label="Public id" value={deployment.public_id} onCopy={copy} />
            <InfoRow label="Blocking endpoint" value={`${API_BASE}/v1/runs/wait`} onCopy={copy} />
            <InfoRow label="Streaming endpoint" value={`${API_BASE}/v1/runs/stream`} onCopy={copy} />
            <div className="moldy-muted-panel flex items-center justify-between gap-3 p-3">
              <div className="min-w-0">
                <p className="text-xs font-medium text-muted-foreground">Limits</p>
                <p className="text-xs text-foreground">Rate and token quotas are being prepared.</p>
              </div>
              <Badge variant="outline">planned</Badge>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">
              Deploy this agent before issuing scoped API keys or calling it from external systems.
            </p>
            <Button
              size="sm"
              disabled={!candidate?.eligible || createDeployment.isPending}
              onClick={deploy}
            >
              <RocketIcon className="size-4" />
              Deploy agent
            </Button>
            {!candidate?.eligible && candidate?.ineligible_reason && (
              <p className="text-xs text-muted-foreground">{candidate.ineligible_reason}</p>
            )}
          </div>
        )}
      </div>

      <div className="moldy-card space-y-3 p-4">
        <div className="flex items-center gap-2">
          <KeyRoundIcon className="size-4 text-muted-foreground" />
          <h3 className="text-sm font-semibold text-foreground">Keys that can call this agent</h3>
        </div>
        <div className="space-y-2">
          {relatedKeys.map((key) => (
            <div key={key.id} className="moldy-muted-panel flex items-center justify-between gap-3 p-3">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-foreground">{key.name}</p>
                <p className="font-mono text-xs text-muted-foreground">
                  {key.prefix}...{key.last_four}
                </p>
              </div>
              <Badge variant={key.revoked_at ? 'outline' : 'secondary'}>
                {key.revoked_at ? 'revoked' : 'active'}
              </Badge>
            </div>
          ))}
          {relatedKeys.length === 0 && (
            <p className="moldy-muted-panel p-3 text-sm text-muted-foreground">
              Create a key from Settings → Agent API after deployment.
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

function InfoRow({
  label,
  value,
  onCopy,
}: {
  label: string
  value: string
  onCopy: (value: string) => Promise<void>
}) {
  return (
    <div className="moldy-muted-panel flex items-center gap-2 p-3">
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium text-muted-foreground">{label}</p>
        <p className="truncate font-mono text-xs text-foreground">{value}</p>
      </div>
      <Button variant="outline" size="icon-sm" onClick={() => onCopy(value)} aria-label="Copy">
        <CopyIcon className="size-4" />
      </Button>
    </div>
  )
}
