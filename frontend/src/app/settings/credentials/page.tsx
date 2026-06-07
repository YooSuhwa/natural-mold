'use client'

import { useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { KeyRound, Plus } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { StatusChip } from '@/components/shared/status-chip'
import { DomainIcon } from '@/components/shared/icon'
import { EmptyState } from '@/components/shared/empty-state'
import { SearchInput } from '@/components/shared/search-input'
import {
  CountedLineTabs,
  ResourceBadge,
  ResourceCardAction,
  ResourceCardMeta,
  ResourceCardSubtext,
  ResourceCardTitle,
  ResourceGrid,
  ResourceListCard,
  ResourcePage,
  ResourcePanel,
  ResourceToolbar,
} from '@/components/shared/resource-layout'
import { Skeleton } from '@/components/ui/skeleton'
import { CredentialCreateModal } from '@/components/credential/credential-create-modal'
import { CredentialDetailDialog } from '@/components/credential/credential-detail-dialog'
import { useCredentials, useCredentialTypes } from '@/lib/hooks/use-credentials'
import { getResourceTone, resourceStatusChipClassName } from '@/lib/resource-tones'
import type { Credential, CredentialDefinition } from '@/lib/types/credential'
import { cn } from '@/lib/utils'
import { SettingsShell } from '../_components/settings-shell'

type CredentialStatusTab = 'all' | 'active' | 'auth_needed' | 'expired' | 'disabled' | 'unknown'

const ALL_TAB = 'all'
const CREDENTIAL_STATUS_TABS: CredentialStatusTab[] = [
  ALL_TAB,
  'active',
  'auth_needed',
  'expired',
  'disabled',
  'unknown',
]

const CREDENTIAL_PROVIDER_LABEL_KEYS: Record<string, string> = {
  anthropic: 'providerLabels.anthropic',
  azure_openai: 'providerLabels.azure_openai',
  coupang_partners: 'providerLabels.coupang_partners',
  dart_api: 'providerLabels.dart_api',
  foresttrip_account: 'providerLabels.foresttrip_account',
  google_genai: 'providerLabels.google_genai',
  google_search: 'providerLabels.google_search',
  google_workspace_oauth2: 'providerLabels.google_workspace_oauth2',
  http_api_key: 'providerLabels.http_api_key',
  http_basic: 'providerLabels.http_basic',
  http_bearer: 'providerLabels.http_bearer',
  kipris_plus_api: 'providerLabels.kipris_plus_api',
  ktx_account: 'providerLabels.ktx_account',
  mcp_oauth2: 'providerLabels.mcp_oauth2',
  naver_search: 'providerLabels.naver_search',
  odsay_api: 'providerLabels.odsay_api',
  openai: 'providerLabels.openai',
  openai_compatible: 'providerLabels.openai_compatible',
  openrouter: 'providerLabels.openrouter',
  srt_account: 'providerLabels.srt_account',
}

const CREDENTIAL_CATEGORY_LABEL_KEYS: Record<string, string> = {
  account: 'categories.account',
  api: 'categories.api',
  general: 'categories.general',
  http: 'categories.http',
  llm: 'categories.llm',
  mcp: 'categories.mcp',
  oauth: 'categories.oauth',
  search: 'categories.search',
  system: 'categories.system',
}

function formatDate(value: string | null): string {
  if (!value) return ''
  return new Date(value).toLocaleDateString()
}

function normalizeStatus(value: string | null | undefined): Exclude<CredentialStatusTab, 'all'> {
  if (
    value === 'active' ||
    value === 'auth_needed' ||
    value === 'expired' ||
    value === 'disabled' ||
    value === 'unknown'
  ) {
    return value
  }
  return 'unknown'
}

export default function CredentialsPage() {
  const t = useTranslations('credentials.page')
  const { data: credentials, isLoading } = useCredentials()
  const { data: definitions } = useCredentialTypes()
  const [createOpen, setCreateOpen] = useState(false)
  const [detailId, setDetailId] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<CredentialStatusTab>(ALL_TAB)
  const [search, setSearch] = useState('')

  const definitionMap = useMemo(() => {
    const map = new Map<string, CredentialDefinition>()
    definitions?.forEach((d) => map.set(d.key, d))
    return map
  }, [definitions])

  const data = useMemo(() => credentials ?? [], [credentials])
  const normalizedSearch = search.trim().toLowerCase()

  const filteredCredentials = useMemo(() => {
    return data.filter((credential) => {
      const status = normalizeStatus(credential.status)
      if (activeTab !== ALL_TAB && status !== activeTab) return false
      if (!normalizedSearch) return true
      const definition = definitionMap.get(credential.definition_key)
      return [
        credential.name,
        credential.definition_key,
        credential.status,
        definition?.display_name,
        definition?.category,
        ...credential.field_keys,
      ]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(normalizedSearch))
    })
  }, [activeTab, data, definitionMap, normalizedSearch])

  function countCredentials(tab: CredentialStatusTab): number {
    return data.filter((credential) => {
      const status = normalizeStatus(credential.status)
      if (tab !== ALL_TAB && status !== tab) return false
      if (!normalizedSearch) return true
      const definition = definitionMap.get(credential.definition_key)
      return [
        credential.name,
        credential.definition_key,
        credential.status,
        definition?.display_name,
        definition?.category,
        ...credential.field_keys,
      ]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(normalizedSearch))
    }).length
  }

  const tabs = CREDENTIAL_STATUS_TABS.map((value) => ({
    value,
    label: t(`tabs.${value}`),
    countLabel: t('count', { count: countCredentials(value) }),
  }))

  const isInitialEmpty = !isLoading && data.length === 0
  const isFilteredEmpty = !isLoading && data.length > 0 && filteredCredentials.length === 0

  function getProviderLabel(
    definitionKey: string,
    definition: CredentialDefinition | undefined,
  ): string {
    const messageKey = CREDENTIAL_PROVIDER_LABEL_KEYS[definitionKey]
    return messageKey ? t(messageKey) : (definition?.display_name ?? definitionKey)
  }

  function getCategoryLabel(definition: CredentialDefinition | undefined): string {
    const category = definition?.category
    if (!category) return ''
    const messageKey = CREDENTIAL_CATEGORY_LABEL_KEYS[category]
    return messageKey ? t(messageKey) : category
  }

  return (
    <SettingsShell wide>
      <ResourcePage
        title={t('title')}
        description={t('description')}
        variant="embedded"
        action={
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="size-4" />
            {t('new')}
          </Button>
        }
      >
        <ResourcePanel>
          {isInitialEmpty ? (
            <ResourcePanel.Body>
              <EmptyState
                icon={<KeyRound className="size-6" />}
                title={t('empty.title')}
                description={t('empty.description')}
                className="bg-card/50"
                action={
                  <Button onClick={() => setCreateOpen(true)}>
                    <Plus className="size-4" />
                    {t('empty.action')}
                  </Button>
                }
              />
            </ResourcePanel.Body>
          ) : (
            <>
              <ResourcePanel.Toolbar>
                <CountedLineTabs
                  ariaLabel={t('tabs.label')}
                  value={activeTab}
                  tabs={tabs}
                  onValueChange={(value) => setActiveTab(value as CredentialStatusTab)}
                />
                <ResourceToolbar>
                  <SearchInput
                    containerClassName="flex-1 sm:max-w-[360px]"
                    placeholder={t('searchPlaceholder')}
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                  />
                </ResourceToolbar>
              </ResourcePanel.Toolbar>

              <ResourcePanel.Body className="bg-background/30">
                {isLoading ? (
                  <ResourceGrid minColumnWidth={252}>
                    {Array.from({ length: 6 }).map((_, index) => (
                      <Skeleton key={index} className="moldy-skeleton-card h-[176px]" />
                    ))}
                  </ResourceGrid>
                ) : isFilteredEmpty ? (
                  <EmptyState title={t('empty.filtered')} className="bg-card/50" />
                ) : (
                  <ResourceGrid minColumnWidth={252}>
                    {filteredCredentials.map((credential) => (
                      <CredentialCard
                        key={credential.id}
                        credential={credential}
                        definition={definitionMap.get(credential.definition_key)}
                        providerLabel={getProviderLabel(
                          credential.definition_key,
                          definitionMap.get(credential.definition_key),
                        )}
                        categoryLabel={getCategoryLabel(
                          definitionMap.get(credential.definition_key),
                        )}
                        fieldCountLabel={t('fieldCount', {
                          count: credential.field_keys.length,
                        })}
                        lastUsedLabel={formatDate(credential.last_used_at)}
                        lastTestedLabel={formatDate(credential.last_tested_at)}
                        sharedLabel={t('shared')}
                        manageLabel={t('actions.manage')}
                        onOpen={setDetailId}
                      />
                    ))}
                  </ResourceGrid>
                )}
              </ResourcePanel.Body>
            </>
          )}
        </ResourcePanel>

        <CredentialCreateModal open={createOpen} onOpenChange={setCreateOpen} />
        <CredentialDetailDialog
          credentialId={detailId}
          open={!!detailId}
          onOpenChange={(open) => !open && setDetailId(null)}
        />
      </ResourcePage>
    </SettingsShell>
  )
}

function CredentialCard({
  credential,
  definition,
  providerLabel,
  categoryLabel,
  fieldCountLabel,
  lastUsedLabel,
  lastTestedLabel,
  sharedLabel,
  manageLabel,
  onOpen,
}: {
  credential: Credential
  definition: CredentialDefinition | undefined
  providerLabel: string
  categoryLabel: string
  fieldCountLabel: string
  lastUsedLabel: string
  lastTestedLabel: string
  sharedLabel: string
  manageLabel: string
  onOpen: (id: string) => void
}) {
  const tone = getResourceTone(definition?.category ?? credential.definition_key)
  const secondaryMetaLabel = credential.is_shared ? sharedLabel : lastTestedLabel || lastUsedLabel

  return (
    <ResourceListCard
      as="button"
      tone={tone}
      density="standard"
      aria-label={`${credential.name} ${providerLabel}`}
      onClick={() => onOpen(credential.id)}
    >
      <ResourceListCard.Header>
        <span className={cn('moldy-resource-icon', tone.icon)}>
          <DomainIcon
            iconId={definition?.icon_id ?? credential.definition_key}
            className="size-4.5"
          />
        </span>
        <ResourceBadge tone={tone}>{providerLabel}</ResourceBadge>
      </ResourceListCard.Header>

      <ResourceCardTitle>{credential.name}</ResourceCardTitle>
      {categoryLabel ? <ResourceCardSubtext>{categoryLabel}</ResourceCardSubtext> : null}

      <ResourceListCard.StatusRow>
        <StatusChip variant={credential.status} className={resourceStatusChipClassName} />
      </ResourceListCard.StatusRow>

      <ResourceListCard.MetaRow>
        <ResourceCardMeta>{fieldCountLabel}</ResourceCardMeta>
        {secondaryMetaLabel ? <ResourceCardMeta>{secondaryMetaLabel}</ResourceCardMeta> : null}
      </ResourceListCard.MetaRow>

      <ResourceListCard.Footer>
        <ResourceCardAction>{manageLabel}</ResourceCardAction>
      </ResourceListCard.Footer>
    </ResourceListCard>
  )
}
