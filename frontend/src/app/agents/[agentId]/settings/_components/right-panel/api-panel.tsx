'use client'

import { CopyIcon, KeyRoundIcon, Loader2Icon, RocketIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
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
  const t = useTranslations('agent.settings.apiPanel')
  const candidates = useAgentDeploymentCandidates()
  const deployments = useAgentDeployments()
  const apiKeys = useAgentApiKeys()
  const createDeployment = useCreateAgentDeployment()

  const deployment = (deployments.data ?? []).find((item) => item.agent_id === agentId)
  const candidate = (candidates.data ?? []).find((item) => item.agent_id === agentId)
  const ineligibleReason =
    candidate?.ineligible_reason_code === 'fixed_identity_required'
      ? t('ineligibleReasons.fixedIdentityRequired')
      : candidate?.ineligible_reason
  const relatedKeys = (apiKeys.data ?? []).filter(
    (key) =>
      key.allow_all_deployments ||
      key.deployments.some((item) => item.deployment_id === deployment?.id),
  )

  async function copy(value: string) {
    await navigator.clipboard.writeText(value)
    toast.success(t('copied'))
  }

  async function deploy() {
    await createDeployment.mutateAsync({ agent_id: agentId })
    toast.success(t('deployedToast'))
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
            <p className="text-xs text-muted-foreground">{t('description')}</p>
          </div>
          {deployment ? (
            <Badge variant="secondary">{t('status.deployed')}</Badge>
          ) : (
            <Badge variant="outline">{t('status.notDeployed')}</Badge>
          )}
        </div>

        {deployment ? (
          <div className="space-y-2">
            <InfoRow
              label={t('publicId')}
              value={deployment.public_id}
              copyAriaLabel={t('copy')}
              onCopy={copy}
            />
            <InfoRow
              label={t('blockingEndpoint')}
              value={`${API_BASE}/v1/runs/wait`}
              copyAriaLabel={t('copy')}
              onCopy={copy}
            />
            <InfoRow
              label={t('streamingEndpoint')}
              value={`${API_BASE}/v1/runs/stream`}
              copyAriaLabel={t('copy')}
              onCopy={copy}
            />
            <div className="moldy-muted-panel flex items-center justify-between gap-3 p-3">
              <div className="min-w-0">
                <p className="text-xs font-medium text-muted-foreground">{t('limits')}</p>
                <p className="text-xs text-foreground">{t('limitsDescription')}</p>
              </div>
              <Badge variant="outline">{t('planned')}</Badge>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">{t('deployDescription')}</p>
            <Button
              size="sm"
              disabled={!candidate?.eligible || createDeployment.isPending}
              onClick={deploy}
            >
              <RocketIcon className="size-4" />
              {t('deployAgent')}
            </Button>
            {!candidate?.eligible && ineligibleReason && (
              <p className="text-xs text-muted-foreground">{ineligibleReason}</p>
            )}
          </div>
        )}
      </div>

      <div className="moldy-card space-y-3 p-4">
        <div className="flex items-center gap-2">
          <KeyRoundIcon className="size-4 text-muted-foreground" />
          <h3 className="text-sm font-semibold text-foreground">{t('keysTitle')}</h3>
        </div>
        <div className="space-y-2">
          {relatedKeys.map((key) => (
            <div
              key={key.id}
              className="moldy-muted-panel flex items-center justify-between gap-3 p-3"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-foreground">{key.name}</p>
                <p className="font-mono text-xs text-muted-foreground">
                  {key.prefix}...{key.last_four}
                </p>
              </div>
              <Badge variant={key.revoked_at ? 'outline' : 'secondary'}>
                {key.revoked_at ? t('keyStatus.revoked') : t('keyStatus.active')}
              </Badge>
            </div>
          ))}
          {relatedKeys.length === 0 && (
            <p className="moldy-muted-panel p-3 text-sm text-muted-foreground">{t('emptyKeys')}</p>
          )}
        </div>
      </div>
    </div>
  )
}

function InfoRow({
  label,
  value,
  copyAriaLabel,
  onCopy,
}: {
  label: string
  value: string
  copyAriaLabel: string
  onCopy: (value: string) => Promise<void>
}) {
  return (
    <div className="moldy-muted-panel flex items-center gap-2 p-3">
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium text-muted-foreground">{label}</p>
        <p className="truncate font-mono text-xs text-foreground">{value}</p>
      </div>
      <Button
        variant="outline"
        size="icon-sm"
        onClick={() => onCopy(value)}
        aria-label={copyAriaLabel}
      >
        <CopyIcon className="size-4" />
      </Button>
    </div>
  )
}
