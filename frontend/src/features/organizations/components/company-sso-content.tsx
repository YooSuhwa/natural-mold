'use client'

import { useEffect, useRef } from 'react'
import { useSearchParams } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { EmptyState } from '@/components/shared/empty-state'
import { ApiError } from '@/lib/api/client'
import { useTenantDomains } from '@/lib/hooks/use-tenant-sso'
import type { OrganizationSummary } from '@/lib/types/organization'
import { CompanySsoConnections } from './company-sso-connections'
import { CompanySsoDomains } from './company-sso-domains'
import { CompanySsoPolicy } from './company-sso-policy'
import { OrgPageFrame } from './org-page-frame'
import { useSsoErrorText } from '../lib/sso-error-text'

export function CompanySsoContent() {
  const t = useTranslations('orgAdmin.sso')
  return (
    <OrgPageFrame title={t('title')} description={t('description')}>
      {(organization) => <CompanySsoBody organization={organization} />}
    </OrgPageFrame>
  )
}

function CompanySsoBody({ organization }: { organization: OrganizationSummary }) {
  const t = useTranslations('orgAdmin.sso')
  const errorText = useSsoErrorText()
  const tenantId = organization.tenant_id
  const domains = useTenantDomains(tenantId)
  const params = useSearchParams()
  const testResult = params.get('result')

  const shown = useRef<string | null>(null)

  useEffect(() => {
    // Shown once per redirect back from the IdP test login.
    if (!testResult || shown.current === testResult) return
    shown.current = testResult
    if (testResult === 'ok') toast.success(t('connections.testPassed'))
    else toast.error(errorText(testResult))
  }, [testResult, t, errorText])

  if (domains.error instanceof ApiError && domains.error.status === 403) {
    return <EmptyState title={t('adminOnly')} description={t('adminOnlyDescription')} />
  }
  return (
    <div className="space-y-4">
      <CompanySsoDomains tenantId={tenantId} />
      <CompanySsoConnections tenantId={tenantId} />
      <CompanySsoPolicy tenantId={tenantId} />
    </div>
  )
}
