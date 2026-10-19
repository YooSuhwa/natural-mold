'use client'

import { useQueryClient } from '@tanstack/react-query'
import { createStore, Provider as JotaiProvider } from 'jotai'
import { useTranslations } from 'next-intl'
import { usePathname, useRouter } from 'next/navigation'
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { flushSync } from 'react-dom'

import { useSwitchActiveOrganization } from '@/lib/hooks/use-organizations'
import { beginOrgSwitch, finishOrgSwitch } from '@/lib/org-scope/org-scope-signal'
import { purgeOrgClientState } from '@/lib/org-scope/purge-org-client-state'
import { hasUnsavedOrgDrafts } from '@/lib/org-scope/unsaved-drafts'
import { organizationQueryKeys } from '@/lib/query-keys/organizations'
import type { OrganizationSummary } from '@/lib/types/organization'

export type OrgSwitchResult = 'switched' | 'cancelled'

interface OrgContextValue {
  readonly isSwitching: boolean
  /** Rejects when the server refuses the switch (the previous org stays). */
  readonly switchOrganization: (orgId: string) => Promise<OrgSwitchResult>
}

const OrgContext = createContext<OrgContextValue | null>(null)

export function useOrgContext(): OrgContextValue {
  const value = useContext(OrgContext)
  if (!value) throw new Error('useOrgContext must be used within OrgContextProvider')
  return value
}

/**
 * Pages that show only the user's own, role-independent settings. Every other
 * route may name a previous-org resource or depend on the previous org's
 * permissions, so a switch lands on home.
 */
const ROLE_INDEPENDENT_SETTINGS = new Set([
  '/settings/appearance',
  '/settings/security',
  '/settings/memory',
])

export function orgSwitchLandingPath(pathname: string | null): string {
  return pathname && ROLE_INDEPENDENT_SETTINGS.has(pathname) ? pathname : '/'
}

/** How long to wait for client navigation before a hard navigation. */
export const LANDING_TIMEOUT_MS = 5_000

type SwitchPhase =
  | { readonly kind: 'idle' }
  /** Org subtree unmounted; purging state and navigating. */
  | { readonly kind: 'purging' }
  /** Purged; waiting for the router to reach the landing path. */
  | { readonly kind: 'landing'; readonly path: string }

/**
 * Plan 13.11 organization switch boundary. Sequence after the server accepts
 * POST /api/me/active-org:
 * 1. close the request gate (in-flight org requests abort, new ones get an
 *    aborted signal) and synchronously unmount the org subtree behind a
 *    neutral placeholder, so no observer can refetch or re-render old data
 *    (stream hooks close their transports on unmount);
 * 2. cancel/remove org-scoped queries, clear org-scoped storage and module
 *    caches, mark the new active org, navigate to the landing route;
 * 3. once the router is on the landing path, open a new request generation and
 *    mount the subtree with a fresh Jotai store, then refetch the org list.
 */
export function OrgContextProvider({ children }: { readonly children: ReactNode }) {
  const t = useTranslations('sidebar.orgSwitcher')
  const router = useRouter()
  const pathname = usePathname()
  const queryClient = useQueryClient()
  const { mutateAsync, isPending } = useSwitchActiveOrganization()
  const [store, setStore] = useState(() => createStore())
  const [phase, setPhase] = useState<SwitchPhase>({ kind: 'idle' })
  const refetchOrgListAfterRemount = useRef(false)
  const isSwitching = isPending || phase.kind !== 'idle'

  const switchOrganization = useCallback(
    async (orgId: string): Promise<OrgSwitchResult> => {
      if (isSwitching) return 'cancelled'
      if (hasUnsavedOrgDrafts() && !window.confirm(t('discardDraftsConfirm'))) {
        return 'cancelled'
      }
      const result = await mutateAsync(orgId)
      const landing = orgSwitchLandingPath(pathname)

      beginOrgSwitch()
      flushSync(() => setPhase({ kind: 'purging' }))
      await purgeOrgClientState(queryClient)
      queryClient.setQueryData<OrganizationSummary[]>(organizationQueryKeys.mine, (current) =>
        current?.map((org) => ({ ...org, is_active: org.id === result.org_id })),
      )
      router.replace(landing)
      setPhase({ kind: 'landing', path: landing })
      return 'switched'
    },
    [isSwitching, mutateAsync, pathname, queryClient, router, t],
  )

  const landed = phase.kind === 'landing' && pathname === phase.path
  const pendingLanding = phase.kind === 'landing' && !landed ? phase.path : null
  useEffect(() => {
    if (pendingLanding === null) return
    // If client navigation never reaches the landing path, reload there so the
    // switching placeholder cannot stay up forever (the reload starts clean).
    const timer = setTimeout(() => window.location.assign(pendingLanding), LANDING_TIMEOUT_MS)
    return () => clearTimeout(timer)
  }, [pendingLanding])
  useEffect(() => {
    if (!landed) return
    finishOrgSwitch()
    /* eslint-disable react-hooks/set-state-in-effect -- remount once the route is safe */
    setStore(createStore())
    setPhase({ kind: 'idle' })
    /* eslint-enable react-hooks/set-state-in-effect */
    refetchOrgListAfterRemount.current = true
  }, [landed])

  // Runs after the new store's subtree has mounted (parent effects follow
  // child effects).
  useEffect(() => {
    if (!refetchOrgListAfterRemount.current) return
    refetchOrgListAfterRemount.current = false
    void queryClient.invalidateQueries({ queryKey: organizationQueryKeys.mine })
  }, [queryClient, store])

  const value = useMemo(
    () => ({ isSwitching, switchOrganization }),
    [isSwitching, switchOrganization],
  )

  return (
    <OrgContext.Provider value={value}>
      {phase.kind === 'idle' ? (
        <JotaiProvider store={store}>{children}</JotaiProvider>
      ) : (
        <div
          role="status"
          aria-live="polite"
          data-testid="org-switching"
          className="flex h-svh w-full items-center justify-center text-sm text-muted-foreground"
        >
          {t('switching')}
        </div>
      )}
    </OrgContext.Provider>
  )
}
