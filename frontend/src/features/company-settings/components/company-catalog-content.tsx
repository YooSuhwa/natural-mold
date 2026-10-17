'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'

import { CountedTabs } from '@/components/shared/counted-tabs'
import { CompanyAdminGate } from './company-admin-gate'
import { CompanyCatalogPolicyTab } from './company-catalog-policy-tab'
import { CompanyCredentialsTab } from './company-credentials-tab'
import { CompanyLlmTab } from './company-llm-tab'
import { CompanyModelsTab } from './company-models-tab'

const TABS = ['models', 'credentials', 'llm', 'catalog'] as const
type Tab = (typeof TABS)[number]

function isTab(value: string): value is Tab {
  return (TABS as readonly string[]).includes(value)
}

/** S07 company models, shared credentials, service LLM and company catalog (admins only). */
export function CompanyCatalogContent() {
  const t = useTranslations('orgAdmin.companySettings.catalogPage')
  const [tab, setTab] = useState<Tab>('models')

  return (
    <CompanyAdminGate title={t('title')} description={t('description')} requireAdmin>
      {({ tenantId }) => (
        <div className="space-y-4">
          <CountedTabs
            value={tab}
            onValueChange={(value) => (isTab(value) ? setTab(value) : undefined)}
            ariaLabel={t('tabsLabel')}
            tabs={TABS.map((value) => ({ value, label: t(`tabs.${value}`) }))}
          />
          {tab === 'models' ? <CompanyModelsTab tenantId={tenantId} /> : null}
          {tab === 'credentials' ? <CompanyCredentialsTab tenantId={tenantId} /> : null}
          {tab === 'llm' ? <CompanyLlmTab tenantId={tenantId} /> : null}
          {tab === 'catalog' ? <CompanyCatalogPolicyTab tenantId={tenantId} /> : null}
        </div>
      )}
    </CompanyAdminGate>
  )
}
