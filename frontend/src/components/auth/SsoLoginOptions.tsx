'use client'

import { useTranslations } from 'next-intl'
import { useState, type FormEvent } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import type { SsoDiscovery, SsoLoginOption } from '@/lib/types/sso'

import { AuthAlert } from './AuthAlert'

interface Props {
  discovery: SsoDiscovery
  onLdapLogin: (option: SsoLoginOption, username: string, password: string) => Promise<void>
  ldapPending: boolean
  ldapError: boolean
}

/** "Continue with the company account" step of the email-first login (S30). */
export function SsoLoginOptions({ discovery, onLdapLogin, ldapPending, ldapError }: Props) {
  const t = useTranslations('auth.sso')
  if (discovery.method !== 'sso') return null
  return (
    <div className="grid gap-2.5" data-testid="sso-login-options">
      {discovery.sso_required ? <AuthAlert variant="info">{t('required')}</AuthAlert> : null}
      {discovery.connections.map((option) =>
        option.protocol === 'oidc' && option.start_url ? (
          <Button
            key={option.id}
            type="button"
            variant="outline"
            className="w-full"
            onClick={() => window.location.assign(option.start_url ?? '/')}
          >
            {t('continueWith', { name: option.name })}
          </Button>
        ) : (
          <LdapForm
            key={option.id}
            option={option}
            onSubmit={onLdapLogin}
            pending={ldapPending}
            error={ldapError}
          />
        ),
      )}
    </div>
  )
}

function LdapForm({
  option,
  onSubmit,
  pending,
  error,
}: {
  option: SsoLoginOption
  onSubmit: Props['onLdapLogin']
  pending: boolean
  error: boolean
}) {
  const t = useTranslations('auth.sso')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')

  function submit(event: FormEvent) {
    event.preventDefault()
    if (!username || !password) return
    void onSubmit(option, username, password).catch(() => {
      // The mutation state renders the uniform error.
    })
  }

  return (
    <form onSubmit={submit} className="grid gap-2 rounded-md border p-3" noValidate>
      <p className="text-sm font-medium">{t('ldapTitle', { name: option.name })}</p>
      {error ? <AuthAlert>{t('ldapFailed')}</AuthAlert> : null}
      <label htmlFor={`ldap-user-${option.id}`} className="text-xs text-muted-foreground">
        {t('username')}
      </label>
      <Input
        id={`ldap-user-${option.id}`}
        autoComplete="username"
        value={username}
        onChange={(event) => setUsername(event.target.value)}
      />
      <label htmlFor={`ldap-password-${option.id}`} className="text-xs text-muted-foreground">
        {t('password')}
      </label>
      <Input
        id={`ldap-password-${option.id}`}
        type="password"
        autoComplete="current-password"
        value={password}
        onChange={(event) => setPassword(event.target.value)}
      />
      <Button type="submit" disabled={pending || !username || !password}>
        {t('ldapSubmit')}
      </Button>
    </form>
  )
}
