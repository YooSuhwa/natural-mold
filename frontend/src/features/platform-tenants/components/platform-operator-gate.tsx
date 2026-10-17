'use client'

import type { ReactNode } from 'react'
import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { LockKeyholeIcon } from 'lucide-react'

import { buttonVariants } from '@/components/ui/button'
import { usePlatformOperatorAccess } from '@/lib/hooks/use-platform-tenants'
import { cn } from '@/lib/utils'

/**
 * S03/S04 are shown only to the platform operator (super user in SaaS; plan 13.16).
 * Fail closed: while the capability is loading or unknown the screen stays hidden.
 * The backend gates every route again (`require_saas_platform_operator`).
 */
export function PlatformOperatorGate({ children }: { children: ReactNode }) {
  const t = useTranslations('appSettings.platformTenants.gate')
  const { pending, allowed } = usePlatformOperatorAccess()
  if (pending) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        {t('loading')}
      </p>
    )
  }
  if (!allowed) {
    return (
      <div role="alert" data-error-kind="not_found" className="moldy-error-state">
        <LockKeyholeIcon className="size-8 text-muted-foreground" aria-hidden="true" />
        <div>
          <p className="text-sm font-semibold text-foreground">{t('deniedTitle')}</p>
          <p className="mt-1 text-sm text-muted-foreground">{t('deniedDescription')}</p>
        </div>
        <Link href="/settings" className={cn(buttonVariants({ size: 'sm', variant: 'outline' }))}>
          {t('back')}
        </Link>
      </div>
    )
  }
  return <>{children}</>
}
