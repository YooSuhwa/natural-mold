'use client'

import { Suspense } from 'react'
import { useSearchParams } from 'next/navigation'
import { useTranslations } from 'next-intl'

import { AuthAlert } from '@/components/auth/AuthAlert'
import { LoginForm } from '@/components/auth/LoginForm'
import { SsoLoginOptions } from '@/components/auth/SsoLoginOptions'
import { useLogin } from '@/lib/hooks/useAuth'
import { useLdapLogin, useSsoDiscovery } from '@/lib/hooks/use-sso-login'

function LoginPageInner() {
  const t = useTranslations('auth.sso')
  const params = useSearchParams()
  const callbackUrl = params.get('callbackUrl')
  const ssoError = params.get('sso_error')
  const linkPending = params.get('sso_link') === 'pending'
  const login = useLogin()
  const discovery = useSsoDiscovery()
  const ldap = useLdapLogin()

  return (
    <>
      {linkPending ? (
        <div className="mb-3.5">
          <AuthAlert variant="info">{t('linkPending')}</AuthAlert>
        </div>
      ) : null}
      {ssoError ? (
        <div className="mb-3.5">
          <AuthAlert>{t('failed')}</AuthAlert>
        </div>
      ) : null}
      <LoginForm
        onSubmit={async (email, password) => {
          await login.mutateAsync({ email, password })
        }}
        isLoading={login.isPending}
        error={login.error}
        showCallbackNotice={Boolean(callbackUrl)}
        onEmailBlur={(email) => discovery.mutate(email)}
      />
      {discovery.data?.method === 'sso' ? (
        <div className="mt-4 grid gap-2">
          <p className="text-center text-xs text-muted-foreground">{t('divider')}</p>
          <SsoLoginOptions
            discovery={discovery.data}
            ldapPending={ldap.isPending}
            ldapError={ldap.isError}
            onLdapLogin={async (option, username, password) => {
              await ldap.mutateAsync({
                connectionId: option.id,
                username,
                password,
                loginCsrf: discovery.data?.login_csrf ?? '',
              })
            }}
          />
        </div>
      ) : null}
    </>
  )
}

export default function LoginPage() {
  // useSearchParams must be wrapped in Suspense for static prerender.
  return (
    <Suspense fallback={null}>
      <LoginPageInner />
    </Suspense>
  )
}
