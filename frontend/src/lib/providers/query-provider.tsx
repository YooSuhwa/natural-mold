'use client'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useRouter } from 'next/navigation'
import { useEffect, useState, type ReactNode } from 'react'
import { useTranslations } from 'next-intl'

import { setSessionExpiredHandler } from '@/lib/api/client'
import { showSessionExpiredToast } from '@/components/auth/session-expired-toast'

export function QueryProvider({ children }: { children: ReactNode }) {
  const router = useRouter()
  const t = useTranslations('auth.errors')

  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30 * 1000,
            retry: 1,
          },
        },
      }),
  )

  // Wire the API client's 401-after-refresh hook to our toast + redirect.
  useEffect(() => {
    const handler = () => {
      try {
        queryClient.clear()
      } catch {
        // ignore
      }
      showSessionExpiredToast({
        title: t('sessionExpired'),
        description: t('sessionExpiredDesc'),
      })
      if (typeof window !== 'undefined') {
        const path = window.location.pathname + window.location.search
        const isAuthRoute = path.startsWith('/login') || path.startsWith('/register')
        if (!isAuthRoute) {
          const callback = encodeURIComponent(path)
          router.push(`/login?callbackUrl=${callback}`)
        }
      }
    }
    setSessionExpiredHandler(handler)
    return () => setSessionExpiredHandler(null)
  }, [queryClient, router, t])

  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}
