'use client'

import { useEffect, type ReactNode } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import { Loader2Icon } from 'lucide-react'

import { useSession } from '@/lib/auth/session'

interface Props {
  children: ReactNode
  fallback?: ReactNode
}

/**
 * Client-side complement to `middleware.ts` — used for routes that need
 * fresh session data (e.g. branching on `is_super_user`). The middleware
 * still handles cookie-only redirects for the initial request.
 */
export function AuthGuard({ children, fallback }: Props) {
  const { data, isLoading, isFetched } = useSession()
  const router = useRouter()
  const pathname = usePathname()

  useEffect(() => {
    if (!isFetched) return
    if (data === null) {
      const callback = encodeURIComponent(pathname || '/')
      router.replace(`/login?callbackUrl=${callback}`)
    }
  }, [data, isFetched, pathname, router])

  if (isLoading || !isFetched) {
    return (
      fallback ?? (
        <div className="flex h-screen items-center justify-center">
          <Loader2Icon className="size-6 animate-spin text-muted-foreground" aria-hidden />
        </div>
      )
    )
  }

  if (!data) return null
  return <>{children}</>
}
