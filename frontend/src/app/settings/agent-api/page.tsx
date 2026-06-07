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
import { useTranslations } from 'next-intl'
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
  const t = useTranslations('appSettings.agentApi')
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
    toast.success(t('toasts.copied', { label }))
  }

  async function deployAgent(agentId: string) {
    await createDeployment.mutateAsync({ agent_id: agentId })
    toast.success(t('toasts.deployed'))
  }

  async function revoke(id: string) {
    await revokeKey.mutateAsync(id)
    toast.success(t('toasts.revoked'))
  }

  const waitEndpoint = `${API_BASE}/v1/runs/wait`
  const streamEndpoint = `${API_BASE}/v1/runs/stream`

  return (
    <SettingsShell>
      <div className="space-y-5">
        <section className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="space-y-1">
            <h2 className="text-lg font-semibold text-foreground">{t('title')}</h2>
            <p className="max-w-2xl text-sm leading-6 text-muted-foreground">{t('description')}</p>
          </div>
          <Button
            onClick={() => setCreateOpen(true)}
            disabled={(deployments.data ?? []).length === 0}
          >
            <KeyRoundIcon className="size-4" />
            {t('actions.createKey')}
          </Button>
        </section>

        <section className="grid gap-3 lg:grid-cols-4">
          <MetricCard
            label={t('metrics.deployments')}
            value={String(deployments.data?.length ?? 0)}
          />
          <MetricCard label={t('metrics.apiKeys')} value={String(apiKeys.data?.length ?? 0)} />
          <MetricCard label={t('metrics.baseUrl')} value={`${API_BASE}/v1`} monospace />
          <MetricCard
            label={t('metrics.limits')}
            value={t('metrics.planned')}
            helper={t('metrics.limitsHelper')}
          />
        </section>

        <section className="moldy-panel space-y-3 p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h3 className="text-sm font-semibold text-foreground">{t('deployments.title')}</h3>
              <p className="text-xs text-muted-foreground">{t('deployments.description')}</p>
            </div>
            {candidates.isLoading && (
              <Loader2Icon className="size-4 animate-spin text-muted-foreground" />
            )}
          </div>

          <div className="space-y-2">
            {(candidates.data ?? []).map((candidate) => {
              const deployment = deploymentByAgent.get(candidate.agent_id)
              const ineligibleReason =
                candidate.ineligible_reason_code === 'fixed_identity_required'
                  ? t('deployments.ineligibleReasons.fixedIdentityRequired')
                  : candidate.ineligible_reason
              return (
                <div key={candidate.agent_id} className="moldy-card flex items-center gap-3 p-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <p className="truncate text-sm font-medium text-foreground">
                        {candidate.agent_name}
                      </p>
                      {deployment ? (
                        <Badge variant="secondary">{t('deployments.status.deployed')}</Badge>
                      ) : candidate.eligible ? (
                        <Badge variant="outline">{t('deployments.status.ready')}</Badge>
                      ) : (
                        <Badge variant="outline">{t('deployments.status.blocked')}</Badge>
                      )}
                    </div>
                    <p className="truncate font-mono text-xs text-muted-foreground">
                      {deployment?.public_id ?? candidate.runtime_name ?? candidate.agent_id}
                    </p>
                    {!candidate.eligible && ineligibleReason && (
                      <p className="text-xs text-muted-foreground">{ineligibleReason}</p>
                    )}
                  </div>
                  {deployment ? (
                    <Button
                      variant="outline"
                      size="icon-sm"
                      onClick={() => copy(`${API_BASE}/v1/runs/wait`, t('examples.endpoint'))}
                      aria-label={t('actions.copyEndpoint')}
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
                      {t('actions.deploy')}
                    </Button>
                  )}
                </div>
              )
            })}
            {!candidates.isLoading && (candidates.data ?? []).length === 0 && (
              <div className="moldy-muted-panel p-4 text-sm text-muted-foreground">
                {t('deployments.empty')}
              </div>
            )}
          </div>
        </section>

        <section className="moldy-panel space-y-3 p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h3 className="text-sm font-semibold text-foreground">{t('keys.title')}</h3>
              <p className="text-xs text-muted-foreground">{t('keys.description')}</p>
            </div>
            {apiKeys.isLoading && (
              <Loader2Icon className="size-4 animate-spin text-muted-foreground" />
            )}
          </div>

          <div className="space-y-2">
            {(apiKeys.data ?? []).map((key) => (
              <div
                key={key.id}
                className="moldy-card flex flex-col gap-3 p-3 md:flex-row md:items-center"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <p className="truncate text-sm font-medium text-foreground">{key.name}</p>
                    {key.revoked_at ? (
                      <Badge variant="outline">{t('keys.status.revoked')}</Badge>
                    ) : (
                      <Badge variant="secondary">{t('keys.status.active')}</Badge>
                    )}
                  </div>
                  <p className="font-mono text-xs text-muted-foreground">
                    {key.prefix}...{key.last_four}
                  </p>
                  <p className="truncate text-xs text-muted-foreground">
                    {key.allow_all_deployments
                      ? t('keys.allDeployments')
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
                  aria-label={t('actions.revokeKey')}
                >
                  <Trash2Icon className="size-4" />
                </Button>
              </div>
            ))}
            {!apiKeys.isLoading && (apiKeys.data ?? []).length === 0 && (
              <div className="moldy-muted-panel p-4 text-sm text-muted-foreground">
                {t('keys.empty')}
              </div>
            )}
          </div>
        </section>

        <section className="moldy-panel space-y-3 p-4">
          <div className="flex items-center gap-2">
            <LinkIcon className="size-4 text-muted-foreground" />
            <h3 className="text-sm font-semibold text-foreground">{t('examples.title')}</h3>
          </div>
          <EndpointRow
            label={t('examples.blocking')}
            value={waitEndpoint}
            copyLabel={t('examples.endpoint')}
            copyAriaLabel={t('actions.copy')}
            onCopy={copy}
          />
          <EndpointRow
            label={t('examples.streaming')}
            value={streamEndpoint}
            copyLabel={t('examples.endpoint')}
            copyAriaLabel={t('actions.copy')}
            onCopy={copy}
          />
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
      <p
        className={
          monospace ? 'truncate font-mono text-sm text-foreground' : 'text-xl font-semibold'
        }
      >
        {value}
      </p>
      {helper && <p className="text-xs text-muted-foreground">{helper}</p>}
    </div>
  )
}

function EndpointRow({
  label,
  value,
  copyLabel,
  copyAriaLabel,
  onCopy,
}: {
  label: string
  value: string
  copyLabel: string
  copyAriaLabel: string
  onCopy: (value: string, label: string) => Promise<void>
}) {
  return (
    <div className="moldy-muted-panel flex items-center gap-3 p-3">
      <CheckCircle2Icon className="size-4 text-status-success" />
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium text-muted-foreground">{label}</p>
        <p className="truncate font-mono text-xs text-foreground">{value}</p>
      </div>
      <Button
        variant="outline"
        size="icon-sm"
        onClick={() => onCopy(value, copyLabel)}
        aria-label={copyAriaLabel}
      >
        <CopyIcon className="size-4" />
      </Button>
    </div>
  )
}
