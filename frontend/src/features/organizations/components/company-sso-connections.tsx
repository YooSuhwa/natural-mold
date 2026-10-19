'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { openExternalUrl } from '@/lib/browser/window-open'
import type { SsoConnection } from '@/lib/types/sso'
import { useTenantSsoConnections, useTenantSsoMutations } from '@/lib/hooks/use-tenant-sso'
import { useSsoErrorText } from '../lib/sso-error-text'
import { CompanySsoConnectionForm } from './company-sso-connection-form'

/** S30 connections: create, test login, activate after a passed test, remove. */
export function CompanySsoConnections({ tenantId }: { tenantId: string }) {
  const t = useTranslations('orgAdmin.sso.connections')
  const connections = useTenantSsoConnections(tenantId)
  const [creating, setCreating] = useState(false)
  const items = connections.data?.items ?? []

  return (
    <SettingsSectionCard
      title={t('title')}
      description={t('description')}
      actions={
        <Button size="sm" onClick={() => setCreating((open) => !open)}>
          {t('add')}
        </Button>
      }
    >
      {creating ? (
        <CompanySsoConnectionForm tenantId={tenantId} onDone={() => setCreating(false)} />
      ) : null}
      {items.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t('empty')}</p>
      ) : (
        <ul className="divide-y">
          {items.map((item) => (
            <ConnectionRow key={item.id} tenantId={tenantId} connection={item} />
          ))}
        </ul>
      )}
    </SettingsSectionCard>
  )
}

function ConnectionRow({ tenantId, connection }: { tenantId: string; connection: SsoConnection }) {
  const t = useTranslations('orgAdmin.sso.connections')
  const errorText = useSsoErrorText()
  const { testConnection, activateConnection, deleteConnection } = useTenantSsoMutations(tenantId)
  const [account, setAccount] = useState({ username: '', password: '' })
  const ldap = connection.protocol === 'ldap'
  const canActivate = connection.status === 'tested'

  function runTest() {
    testConnection.mutate(
      { id: connection.id, account: ldap ? account : undefined },
      {
        onSuccess: (response) => {
          if (response.authorize_url) {
            openExternalUrl(response.authorize_url)
            return
          }
          if (response.result?.ok) toast.success(t('testPassed'))
          else toast.error(errorText(response.result?.error ?? ''))
        },
        onError: (error) => toast.error(errorText(error)),
      },
    )
  }

  return (
    <li className="grid gap-2 py-3" data-testid={`sso-connection-${connection.id}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{ldap ? t('ldap') : t('oidc')}</span>
        <Badge variant={connection.status === 'active' ? 'default' : 'secondary'}>
          {t(`status.${connection.status}`)}
        </Badge>
        <span className="truncate text-xs text-muted-foreground">
          {ldap ? connection.server_url : connection.issuer}
        </span>
      </div>
      {connection.redirect_uri ? (
        <p className="text-xs text-muted-foreground">
          {t('redirectUri')}{' '}
          <code className="rounded bg-muted px-1.5 py-0.5">{connection.redirect_uri}</code>
        </p>
      ) : null}
      {ldap ? (
        <div className="flex flex-wrap gap-2">
          <Input
            aria-label={t('testUsername')}
            placeholder={t('testUsername')}
            className="w-40"
            value={account.username}
            onChange={(event) => setAccount((prev) => ({ ...prev, username: event.target.value }))}
          />
          <Input
            aria-label={t('testPassword')}
            placeholder={t('testPassword')}
            type="password"
            className="w-40"
            value={account.password}
            onChange={(event) => setAccount((prev) => ({ ...prev, password: event.target.value }))}
          />
        </div>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" disabled={testConnection.isPending} onClick={runTest}>
          {t('test')}
        </Button>
        <Button
          size="sm"
          disabled={!canActivate || activateConnection.isPending}
          title={canActivate ? undefined : t('activateHint')}
          onClick={() =>
            activateConnection.mutate(connection.id, {
              onError: (error) => toast.error(errorText(error)),
            })
          }
        >
          {t('activate')}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={deleteConnection.isPending}
          onClick={() =>
            deleteConnection.mutate(connection.id, {
              onError: (error) => toast.error(errorText(error)),
            })
          }
        >
          {t('remove')}
        </Button>
      </div>
    </li>
  )
}
