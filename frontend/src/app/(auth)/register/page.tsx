'use client'

import { RegisterForm } from '@/components/auth/RegisterForm'
import { useRegister } from '@/lib/hooks/useAuth'

export default function RegisterPage() {
  const register = useRegister()
  return (
    <RegisterForm
      onSubmit={async (data) => {
        await register.mutateAsync(data)
      }}
      isLoading={register.isPending}
      error={register.error}
    />
  )
}
