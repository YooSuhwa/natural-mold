'use client'

import { Suspense } from 'react'
import { useSearchParams } from 'next/navigation'

import { LoginForm } from '@/components/auth/LoginForm'
import { useLogin } from '@/lib/hooks/useAuth'

function LoginPageInner() {
  const params = useSearchParams()
  const callbackUrl = params.get('callbackUrl')
  const login = useLogin()

  return (
    <LoginForm
      onSubmit={async (email, password) => {
        await login.mutateAsync({ email, password })
      }}
      isLoading={login.isPending}
      error={login.error}
      showCallbackNotice={Boolean(callbackUrl)}
    />
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
