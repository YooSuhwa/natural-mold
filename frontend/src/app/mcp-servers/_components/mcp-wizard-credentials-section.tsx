'use client'

import { Activity, KeyRound, Loader2, Plus } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { CredentialPicker } from '@/components/credential/credential-picker'
import { Button } from '@/components/ui/button'

import type { McpProbeState } from './mcp-wizard-form-state'

type McpWizardCredentialsSectionProps = {
  readonly credentialId: string | null
  readonly onCredentialChange: (credentialId: string | null) => void
  readonly credentialDefinitionFilter: string | null
  readonly usesMcpOAuth: boolean
  readonly onCreateOAuthCredential: () => void
  readonly onConnectOAuth: () => void
  readonly oauthStarting: boolean
  readonly oauthWaiting: boolean
  readonly oauthConnected: boolean
  readonly probeState: McpProbeState
  readonly onTest: () => void
  readonly testing: boolean
}

export function McpWizardCredentialsSection({
  credentialId,
  onCredentialChange,
  credentialDefinitionFilter,
  usesMcpOAuth,
  onCreateOAuthCredential,
  onConnectOAuth,
  oauthStarting,
  oauthWaiting,
  oauthConnected,
  probeState,
  onTest,
  testing,
}: McpWizardCredentialsSectionProps) {
  const t = useTranslations('mcp.wizard.auth')
  return (
    <div className="space-y-4">
      <div className="space-y-1.5">
        <label>{t('credential')}</label>
        <CredentialPicker
          value={credentialId}
          onChange={onCredentialChange}
          definitionKeys={credentialDefinitionFilter ? [credentialDefinitionFilter] : undefined}
        />
      </div>

      {usesMcpOAuth ? (
        <div className="rounded-md border border-border/60 bg-muted/30 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <Button type="button" variant="outline" onClick={onCreateOAuthCredential}>
              <Plus className="size-3.5" />
              {t('createOAuthCredential')}
            </Button>
            <Button
              type="button"
              onClick={onConnectOAuth}
              disabled={!credentialId || oauthStarting}
            >
              {oauthStarting ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <KeyRound className="size-3.5" />
              )}
              {t('connectOAuth')}
            </Button>
          </div>
          {oauthConnected ? (
            <p className="mt-2 text-xs text-status-success">{t('oauthConnected')}</p>
          ) : null}
          {oauthWaiting ? (
            <p className="mt-2 text-xs text-muted-foreground">{t('oauthWaiting')}</p>
          ) : null}
        </div>
      ) : null}

      <div className="rounded-md border border-border/60 bg-muted/30 p-3 text-xs text-muted-foreground">
        <p className="font-medium text-foreground">{t('interpolation')}</p>
        <p className="mt-1">
          {t('interpolationBody')}{' '}
          <code className="rounded bg-background px-1 py-0.5 font-mono">
            {'{{ $credentials.<field> }}'}
          </code>
          . {t('interpolationSuffix')}
        </p>
        {credentialDefinitionFilter ? (
          <p className="mt-2">{t('filtered', { type: credentialDefinitionFilter })}</p>
        ) : null}
      </div>

      <div className="flex items-center gap-2">
        <Button onClick={onTest} disabled={testing} variant="outline">
          {testing ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Activity className="size-3.5" />
          )}
          {t('test')}
        </Button>
        {probeState.kind === 'ok' ? (
          <span className="text-xs text-status-success">
            {t('connected', { count: probeState.toolCount })}
          </span>
        ) : null}
        {probeState.kind === 'error' ? (
          <span className="text-xs text-status-danger">{probeState.message}</span>
        ) : null}
      </div>
    </div>
  )
}
