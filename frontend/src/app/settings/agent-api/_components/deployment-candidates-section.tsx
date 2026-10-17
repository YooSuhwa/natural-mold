'use client'

import { CopyIcon, Loader2Icon, RocketIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { ApiErrorState } from '@/components/shared/api-error-state'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type { AgentDeployment, AgentDeploymentCandidate } from '@/lib/types'

interface CandidatesQuery {
  data?: AgentDeploymentCandidate[]
  isLoading: boolean
  isError: boolean
  error: unknown
  refetch: () => unknown
}

/**
 * Deployment candidates (plan 13.9 "배포 생성은 can_deploy 에이전트만 후보"): the server
 * answers `CANDIDATES(agent, can_deploy)`, so the list is shown exactly as returned.
 */
export function DeploymentCandidatesSection({
  candidates,
  deploymentByAgent,
  deployPending,
  onDeploy,
  onCopyEndpoint,
}: {
  candidates: CandidatesQuery
  deploymentByAgent: ReadonlyMap<string, AgentDeployment>
  deployPending: boolean
  onDeploy: (agentId: string) => Promise<void>
  onCopyEndpoint: () => void
}) {
  const t = useTranslations('appSettings.agentApi')
  return (
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
                  onClick={onCopyEndpoint}
                  aria-label={t('actions.copyEndpoint')}
                >
                  <CopyIcon className="size-4" />
                </Button>
              ) : (
                <Button
                  size="sm"
                  disabled={!candidate.eligible || deployPending}
                  onClick={() => void onDeploy(candidate.agent_id)}
                >
                  <RocketIcon className="size-4" />
                  {t('actions.deploy')}
                </Button>
              )}
            </div>
          )
        })}
        {candidates.isError ? (
          <ApiErrorState error={candidates.error} onRetry={() => void candidates.refetch()} />
        ) : null}
        {!candidates.isLoading && !candidates.isError && (candidates.data ?? []).length === 0 && (
          <div className="moldy-muted-panel p-4 text-sm text-muted-foreground">
            {t('deployments.empty')}
          </div>
        )}
      </div>
    </section>
  )
}
