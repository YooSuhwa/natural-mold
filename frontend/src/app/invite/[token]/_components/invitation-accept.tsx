'use client'

import Link from 'next/link'
import { useQuery } from '@tanstack/react-query'
import { MailOpenIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { ApiErrorState } from '@/components/shared/api-error-state'
import { Button, buttonVariants } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { getAuthPageSession } from '@/lib/auth/auth-page-session'
import { SESSION_QUERY_KEY } from '@/lib/auth/session'
import { useAcceptInvitation } from '@/lib/hooks/use-invitation-accept'
import { useLogout } from '@/lib/hooks/useAuth'
import type { User } from '@/lib/types/user'
import { inviteFailure } from '@/features/organizations/lib/invite-error'

/** S12 초대 수락 (`/invite/[token]`, public route): sign up, log in, or join. */
export function InvitationAccept({ token }: { token: string }) {
  const t = useTranslations('auth.invite')
  // Raw session read: a 401 here means "logged out", not an expired-session storm.
  const session = useQuery<User | null>({
    queryKey: SESSION_QUERY_KEY,
    queryFn: getAuthPageSession,
    staleTime: 5 * 60 * 1000,
    retry: false,
  })

  return (
    <main className="flex min-h-svh items-center justify-center bg-background p-4">
      <div className="moldy-card w-full max-w-md space-y-4 p-6">
        <div className="flex items-center gap-2">
          <MailOpenIcon className="size-5 text-primary-strong" aria-hidden="true" />
          <h1 className="moldy-ui-subtitle text-foreground">{t('title')}</h1>
        </div>
        {session.isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : session.data ? (
          <AcceptPanel token={token} user={session.data} />
        ) : (
          <LoggedOutPanel token={token} />
        )}
      </div>
    </main>
  )
}

function LoggedOutPanel({ token }: { token: string }) {
  const t = useTranslations('auth.invite')
  const back = `/invite/${encodeURIComponent(token)}`
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">{t('loggedOut')}</p>
      <div className="flex flex-col gap-2">
        <Link
          href={`/register?invitation=${encodeURIComponent(token)}`}
          className={buttonVariants()}
        >
          {t('register')}
        </Link>
        <Link
          href={`/login?callbackUrl=${encodeURIComponent(back)}`}
          className={buttonVariants({ variant: 'outline' })}
        >
          {t('login')}
        </Link>
      </div>
    </div>
  )
}

function AcceptPanel({ token, user }: { token: string; user: User }) {
  const t = useTranslations('auth.invite')
  const accept = useAcceptInvitation()
  const logout = useLogout()
  const failure = accept.isError ? inviteFailure(accept.error) : null

  if (accept.isSuccess) {
    return <p className="text-sm">{t('joined')}</p>
  }
  if (accept.isError && !failure) {
    return <ApiErrorState error={accept.error} onRetry={() => accept.reset()} />
  }
  if (failure) {
    return (
      <div role="alert" className="space-y-3">
        <p className="text-sm font-semibold text-foreground">{t(`failure.${failure}.title`)}</p>
        <p className="text-sm text-muted-foreground">
          {t(`failure.${failure}.description`, { email: user.email })}
        </p>
        <div className="flex flex-wrap gap-2">
          {failure === 'mismatch' ? (
            <Button onClick={() => logout.mutate()} disabled={logout.isPending}>
              {t('switchAccount')}
            </Button>
          ) : null}
          <Link href="/" className={buttonVariants({ variant: 'outline' })}>
            {t('home')}
          </Link>
        </div>
      </div>
    )
  }
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">{t('signedInAs', { email: user.email })}</p>
      <Button
        className="w-full"
        disabled={accept.isPending}
        onClick={() =>
          accept.mutate(token, {
            // Full reload: the server switched the current organization (13.11 reset).
            onSuccess: () => window.location.assign('/'),
          })
        }
      >
        {t('join')}
      </Button>
    </div>
  )
}
