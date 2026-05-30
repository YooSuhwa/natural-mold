'use client'

import { useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { PartyPopperIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { DialogShell } from '@/components/shared/dialog-shell'
import { useSession } from '@/lib/auth/session'
import { ONBOARDING_DISMISSED_FLAG } from '@/lib/auth/session-flags'
import { useSuperUserWelcomeToast } from './use-super-user-welcome-toast'
import type { User } from '@/lib/types/user'

function shouldShow(user: User): boolean {
  if (typeof window === 'undefined') return false
  let dismissed: string | null = null
  try {
    dismissed = sessionStorage.getItem(ONBOARDING_DISMISSED_FLAG)
  } catch {
    return false
  }
  if (dismissed === '1') return false
  const createdAt = user.created_at ? new Date(user.created_at).getTime() : 0
  return Date.now() - createdAt < 5 * 60 * 1000
}

/**
 * Self-deciding root — reads session and only mounts the inner dialog when
 * onboarding should fire. The Inner component owns its own `open` state and
 * remounts via `key={user.id}` so we never need to sync prop→state in an
 * effect (React 19 anti-pattern, see frontend/AGENTS.md).
 */
export function OnboardingDialog() {
  const { data: user } = useSession()
  if (!user) return null
  return <OnboardingDialogInner key={user.id} user={user} />
}

function OnboardingDialogInner({ user }: { user: User }) {
  const t = useTranslations('auth.onboarding')
  const router = useRouter()
  // Initial visibility computed once — no effect-driven setState.
  const initialOpen = useMemo(() => shouldShow(user), [user])
  const [open, setOpen] = useState(initialOpen)

  // Fire the super-user welcome toast as a side-effect of mount, gated on
  // sessionStorage so it only happens once per session.
  useSuperUserWelcomeToast(user)

  function dismiss() {
    try {
      sessionStorage.setItem(ONBOARDING_DISMISSED_FLAG, '1')
    } catch {
      // ignore
    }
    setOpen(false)
  }

  function handleRegister() {
    dismiss()
    router.push('/credentials')
  }

  return (
    <DialogShell
      open={open}
      onOpenChange={(v) => (v ? setOpen(true) : dismiss())}
      size="md"
      height="auto"
    >
      <DialogShell.Header
        icon={
          <span className="flex size-9 items-center justify-center rounded-lg bg-status-accent/15 text-status-accent">
            <PartyPopperIcon className="size-5" aria-hidden />
          </span>
        }
        title={t('title')}
        description={t('subtitle')}
      />
      <div className="flex-1 overflow-y-auto px-6 py-5">
        <div className="space-y-6 text-sm">
          <p>{t('body')}</p>
          <div className="rounded-lg border border-border/60 bg-muted/40 p-4 space-y-2">
            <p className="font-medium">{t('providers')}</p>
            <ul className="list-disc pl-5 text-muted-foreground space-y-0.5">
              <li>{t('providerList.openai')}</li>
              <li>{t('providerList.anthropic')}</li>
              <li>{t('providerList.google')}</li>
            </ul>
          </div>
          <p className="text-muted-foreground">{t('encryptedNote')}</p>
        </div>
      </div>
      <DialogShell.Footer>
        <Button variant="ghost" onClick={dismiss}>
          {t('later')}
        </Button>
        <Button onClick={handleRegister}>{t('register')}</Button>
      </DialogShell.Footer>
    </DialogShell>
  )
}
