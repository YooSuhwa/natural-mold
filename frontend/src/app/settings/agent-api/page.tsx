'use client'

import { useMemo, useState } from 'react'
import {
  CheckCircle2Icon,
  CopyIcon,
  KeyRoundIcon,
  LinkIcon,
  Loader2Icon,
  RocketIcon,
  Trash2Icon,
} from 'lucide-react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { API_BASE } from '@/lib/api/client'
import {
  useAgentApiKeys,
  useAgentDeploymentCandidates,
  useAgentDeployments,
  useCreateAgentApiKey,
  useCreateAgentDeployment,
  useRevokeAgentApiKey,
} from '@/lib/hooks/use-agent-api'
import type { AgentApiKeyCreated, AgentDeployment } from '@/lib/types'
import { SettingsShell } from '../_components/settings-shell'
import { ApiKeyCreateDialog } from './_components/api-key-create-dialog'
import { ApiKeyCreatedDialog } from './_components/api-key-created-dialog'

export default function AgentApiSettingsPage() {
  const [createOpen, setCreateOpen] = useState(false)
  const [createdKey, setCreatedKey] = useState<AgentApiKeyCreated | null>(null)
  const candidates = useAgentDeploymentCandidates()
  const deployments = useAgentDeployments()
  const apiKeys = useAgentApiKeys()
  const createDeployment = useCreateAgentDeployment()
  const createKey = useCreateAgentApiKey()
  const revokeKey = useRevokeAgentApiKey()

  const deploymentByAgent = useMemo(() => {
    const map = new Map<string, AgentDeployment>()
    for (const deployment of deployments.data ?? []) {
      map.set(deployment.agent_id, deployment)
    }
    return map
  }, [deployments.data])

  async function copy(value: string, label: string) {
    await navigator.clipboard.writeText(value)
    toast.success(`${label} copied`)
  }

  async function deployAgent(agentId: string) {
    await createDeployment.mutateAsync({ agent_id: agentId })
    toast.success('Agent deployed')
  }

  async function revoke(id: string) {
    await revokeKey.mutateAsync(id)
    toast.success('API key revoked')
  }

  const waitEndpoint = `${API_BASE}/v1/runs/wait`
  const streamEndpoint = `${API_BASE}/v1/runs/stream`

  return (
    <SettingsShell>
      <div className="space-y-5">
        <section className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="space-y-1">
            <h2 className="text-lg font-semibold text-foreground">Agent API</h2>
            <p className="max-w-2xl text-sm leading-6 text-muted-foreground">
              Deploy agents, issue server-side API keys, and call Moldy from external systems.
            </p>
          </div>
          <Button onClick={() => setCreateOpen(true)} disabled={(deployments.data ?? []).length === 0}>
            <KeyRoundIcon className="size-4" />
            API key
          </Button>
        </section>

        <section className="grid gap-3 lg:grid-cols-4">
          <MetricCard label="Deployments" value={String(deployments.data?.length ?? 0)} />
          <MetricCard label="API keys" value={String(apiKeys.data?.length ?? 0)} />
          <MetricCard label="Base URL" value={`${API_BASE}/v1`} monospace />
          <MetricCard label="Limits" value="planned" helper="Rate and token quotas are being prepared." />
        </section>

        <section className="moldy-panel space-y-3 p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h3 className="text-sm font-semibold text-foreground">Deployment candidates</h3>
              <p className="text-xs text-muted-foreground">
                Only fixed-identity agents can be deployed to external APIs.
              </p>
            </div>
            {candidates.isLoading && <Loader2Icon className="size-4 animate-spin text-muted-foreground" />}
          </div>

          <div className="space-y-2">
            {(candidates.data ?? []).map((candidate) => {
              const deployment = deploymentByAgent.get(candidate.agent_id)
              return (
                <div key={candidate.agent_id} className="moldy-card flex items-center gap-3 p-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <p className="truncate text-sm font-medium text-foreground">
                        {candidate.agent_name}
                      </p>
                      {deployment ? (
                        <Badge variant="secondary">deployed</Badge>
                      ) : candidate.eligible ? (
                        <Badge variant="outline">ready</Badge>
                      ) : (
                        <Badge variant="outline">blocked</Badge>
                      )}
                    </div>
                    <p className="truncate font-mono text-xs text-muted-foreground">
                      {deployment?.public_id ?? candidate.runtime_name ?? candidate.agent_id}
                    </p>
                    {!candidate.eligible && (
                      <p className="text-xs text-muted-foreground">{candidate.ineligible_reason}</p>
                    )}
                  </div>
                  {deployment ? (
                    <Button
                      variant="outline"
                      size="icon-sm"
                      onClick={() => copy(`${API_BASE}/v1/runs/wait`, 'Endpoint')}
                      aria-label="Copy endpoint"
                    >
                      <CopyIcon className="size-4" />
                    </Button>
                  ) : (
                    <Button
                      size="sm"
                      disabled={!candidate.eligible || createDeployment.isPending}
                      onClick={() => deployAgent(candidate.agent_id)}
                    >
                      <RocketIcon className="size-4" />
                      Deploy
                    </Button>
                  )}
                </div>
              )
            })}
            {!candidates.isLoading && (candidates.data ?? []).length === 0 && (
              <div className="moldy-muted-panel p-4 text-sm text-muted-foreground">
                Create an agent before deploying an API endpoint.
              </div>
            )}
          </div>
        </section>

        <section className="moldy-panel space-y-3 p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h3 className="text-sm font-semibold text-foreground">API keys</h3>
              <p className="text-xs text-muted-foreground">
                Keys are shown once and should only be used from server-side code.
              </p>
            </div>
            {apiKeys.isLoading && <Loader2Icon className="size-4 animate-spin text-muted-foreground" />}
          </div>

          <div className="space-y-2">
            {(apiKeys.data ?? []).map((key) => (
              <div key={key.id} className="moldy-card flex flex-col gap-3 p-3 md:flex-row md:items-center">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <p className="truncate text-sm font-medium text-foreground">{key.name}</p>
                    {key.revoked_at ? (
                      <Badge variant="outline">revoked</Badge>
                    ) : (
                      <Badge variant="secondary">active</Badge>
                    )}
                  </div>
                  <p className="font-mono text-xs text-muted-foreground">
                    {key.prefix}...{key.last_four}
                  </p>
                  <p className="truncate text-xs text-muted-foreground">
                    {key.allow_all_deployments
                      ? 'All deployed agents'
                      : key.deployments.map((deployment) => deployment.agent_name).join(', ')}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-1">
                  {key.scopes.map((scope) => (
                    <Badge key={scope} variant="outline">
                      {scope}
                    </Badge>
                  ))}
                </div>
                <Button
                  variant="destructive"
                  size="icon-sm"
                  disabled={Boolean(key.revoked_at) || revokeKey.isPending}
                  onClick={() => revoke(key.id)}
                  aria-label="Revoke API key"
                >
                  <Trash2Icon className="size-4" />
                </Button>
              </div>
            ))}
            {!apiKeys.isLoading && (apiKeys.data ?? []).length === 0 && (
              <div className="moldy-muted-panel p-4 text-sm text-muted-foreground">
                Deploy an agent, then create an API key.
              </div>
            )}
          </div>
        </section>

        <section className="moldy-panel space-y-3 p-4">
          <div className="flex items-center gap-2">
            <LinkIcon className="size-4 text-muted-foreground" />
            <h3 className="text-sm font-semibold text-foreground">Call examples</h3>
          </div>
          <EndpointRow label="Blocking run" value={waitEndpoint} onCopy={copy} />
          <EndpointRow label="Streaming run" value={streamEndpoint} onCopy={copy} />
        </section>
      </div>

      <ApiKeyCreateDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        deployments={deployments.data ?? []}
        onCreate={(data) => createKey.mutateAsync(data)}
        onCreated={(key) => setCreatedKey(key)}
      />
      <ApiKeyCreatedDialog
        open={createdKey !== null}
        createdKey={createdKey}
        onOpenChange={(open) => {
          if (!open) setCreatedKey(null)
        }}
      />
    </SettingsShell>
  )
}
function MetricCard({
  label,
  value,
  helper,
  monospace = false,
}: {
  label: string
  value: string
  helper?: string
  monospace?: boolean
}) {
  return (
    <div className="moldy-card space-y-1 p-4">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <p className={monospace ? 'truncate font-mono text-sm text-foreground' : 'text-xl font-semibold'}>
        {value}
      </p>
      {helper && <p className="text-xs text-muted-foreground">{helper}</p>}
    </div>
  )
}

function EndpointRow({
  label,
  value,
  onCopy,
}: {
  label: string
  value: string
  onCopy: (value: string, label: string) => Promise<void>
}) {
  return (
    <div className="moldy-muted-panel flex items-center gap-3 p-3">
      <CheckCircle2Icon className="size-4 text-status-success" />
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium text-muted-foreground">{label}</p>
        <p className="truncate font-mono text-xs text-foreground">{value}</p>
      </div>
      <Button variant="outline" size="icon-sm" onClick={() => onCopy(value, label)} aria-label="Copy">
        <CopyIcon className="size-4" />
      </Button>
    </div>
  )
}
