'use client'

import { Fragment, type ReactNode } from 'react'
import { useTranslations } from 'next-intl'

import { EmptyState } from '@/components/shared/empty-state'
import { Skeleton } from '@/components/ui/skeleton'
import { useActiveOrganization } from '@/lib/hooks/use-organizations'
import type { OrganizationSummary } from '@/lib/types/organization'

interface OrgPageFrameProps {
  title: string
  description: string
  children: (organization: OrganizationSummary) => ReactNode
}

/** Settings page frame scoped to the active organization. */
export function OrgPageFrame({ title, description, children }: OrgPageFrameProps) {
  const t = useTranslations('orgAdmin.common')
  const { activeOrganization, isLoading } = useActiveOrganization()

  return (
    <div className="space-y-4">
      <div className="space-y-1">
        <h2 className="text-lg font-semibold text-foreground">{title}</h2>
        <p className="text-sm leading-6 text-muted-foreground">{description}</p>
      </div>
      {isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : activeOrganization ? (
        // Keyed by org so pager cursors and local edits never leak across a switch.
        <Fragment key={activeOrganization.id}>{children(activeOrganization)}</Fragment>
      ) : (
        <EmptyState title={t('noOrganization')} description={t('noOrganizationDescription')} />
      )}
    </div>
  )
}
