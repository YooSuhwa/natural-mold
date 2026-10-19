'use client'

import { useRouter } from 'next/navigation'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { ssoApi } from '@/lib/api/sso'
import { isSafeCallback, persistAuth } from '@/lib/hooks/useAuth'

/** Email-first discovery (plan 10.10): which login the email's company uses. */
export function useSsoDiscovery() {
  return useMutation({ mutationFn: (email: string) => ssoApi.discover(email) })
}

interface LdapLoginInput {
  connectionId: string
  username: string
  password: string
  loginCsrf: string
}

/** LDAP/AD login → the same session as the password login, then the callback URL. */
export function useLdapLogin() {
  const router = useRouter()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: LdapLoginInput) =>
      ssoApi.ldapLogin(input.connectionId, input.username, input.password, input.loginCsrf),
    onSuccess: (res) => {
      persistAuth(res, queryClient)
      const params =
        typeof window !== 'undefined' ? new URLSearchParams(window.location.search) : null
      const callback = params?.get('callbackUrl')
      router.push(isSafeCallback(callback) ? callback : '/')
    },
  })
}
