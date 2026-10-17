'use client'

import { Suspense } from 'react'
import { useSearchParams } from 'next/navigation'

import { RegisterForm } from '@/components/auth/RegisterForm'
import { useRegister } from '@/lib/hooks/useAuth'

function RegisterPageInner() {
  const register = useRegister()
  // S12 "가입하고 참여하기": the invite page links here with its one-time token.
  const invitation = useSearchParams().get('invitation')
  return (
    <RegisterForm
      onSubmit={async (data) => {
        await register.mutateAsync(invitation ? { ...data, invitation_token: invitation } : data)
      }}
      isLoading={register.isPending}
      error={register.error}
    />
  )
}

export default function RegisterPage() {
  // useSearchParams must be wrapped in Suspense for static prerender.
  return (
    <Suspense fallback={null}>
      <RegisterPageInner />
    </Suspense>
  )
}
