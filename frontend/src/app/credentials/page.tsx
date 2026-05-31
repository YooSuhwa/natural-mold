'use client'

import { useMemo, useState, type KeyboardEvent } from 'react'
import { useTranslations } from 'next-intl'
import { ChevronRightIcon, KeyRound, Plus } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { StatusChip } from '@/components/shared/status-chip'
import { DomainIcon } from '@/components/shared/icon'
import { EmptyState } from '@/components/shared/empty-state'
import { SearchInput } from '@/components/shared/search-input'
import {
  CountedLineTabs,
  ResourceGrid,
  ResourcePage,
  ResourcePanel,
  ResourceToolbar,
} from '@/components/shared/resource-layout'
import { Skeleton } from '@/components/ui/skeleton'
import { CredentialCreateModal } from '@/components/credential/credential-create-modal'
import { CredentialDetailDialog } from '@/components/credential/credential-detail-dialog'
import { useCredentials, useCredentialTypes } from '@/lib/hooks/use-credentials'
import type { Credential, CredentialDefinition } from '@/lib/types/credential'
import { cn } from '@/lib/utils'

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
    <ResourcePage
      title={t('title')}
      description={t('description')}
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
                    <Skeleton key={index} className="h-[178px] rounded-md" />
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
                      categoryLabel={getCategoryLabel(definitionMap.get(credential.definition_key))}
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
  const tone = pickCredentialCardTone(
    `${credential.definition_key}:${credential.name}:${credential.status}`,
  )

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== 'Enter' && event.key !== ' ') return
    event.preventDefault()
    onOpen(credential.id)
  }

  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`${credential.name} ${providerLabel}`}
      onClick={() => onOpen(credential.id)}
      onKeyDown={handleKeyDown}
      className={cn(credentialCardClassName(tone))}
    >
      <div className="flex items-start justify-between gap-3">
        <span
          className={cn(
            'inline-flex size-9 shrink-0 items-center justify-center rounded-lg',
            tone.icon,
          )}
        >
          <DomainIcon
            iconId={definition?.icon_id ?? credential.definition_key}
            className="size-4.5"
          />
        </span>
        <span
          className={cn(
            'inline-flex min-w-0 max-w-[132px] items-center gap-1 rounded-md border px-2 py-1 text-[11px] font-semibold leading-none',
            tone.badge,
          )}
        >
          <span className={cn('size-1.5 shrink-0 rounded-full', tone.dot)} />
          <span className="truncate">{providerLabel}</span>
        </span>
      </div>

      <span className="mt-3 line-clamp-1 text-[15px] font-bold leading-tight text-foreground">
        {credential.name}
      </span>
      {categoryLabel ? (
        <p className="mt-2 truncate text-[11px] font-medium text-muted-foreground/80">
          {categoryLabel}
        </p>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <StatusChip
          variant={credential.status}
          className="max-w-[128px] bg-white/55 text-[10.5px] shadow-sm ring-white/80 dark:bg-white/10 dark:ring-white/10"
        />
        <span className={credentialMetaClassName}>{fieldCountLabel}</span>
        {credential.is_shared ? (
          <span className={credentialMetaClassName}>{sharedLabel}</span>
        ) : null}
      </div>

      <div className="mt-3 space-y-1 text-[11px] text-muted-foreground">
        {lastUsedLabel ? <p>{lastUsedLabel}</p> : null}
        {lastTestedLabel ? <p>{lastTestedLabel}</p> : null}
      </div>

      <div className="mt-auto flex items-center justify-end pt-3">
        <span
          className={cn(
            'inline-flex items-center gap-0.5 text-xs font-semibold text-muted-foreground transition-all duration-150',
            'group-hover:translate-x-0.5 group-hover:text-[var(--primary-strong)]',
            'group-focus-visible:translate-x-0.5 group-focus-visible:text-[var(--primary-strong)]',
          )}
        >
          {manageLabel}
          <ChevronRightIcon aria-hidden className="size-3" />
        </span>
      </div>
    </div>
  )
}

type CredentialCardTone = {
  card: string
  icon: string
  badge: string
  dot: string
}

const CREDENTIAL_CARD_TONES: CredentialCardTone[] = [
  {
    card: 'bg-violet-50/75 hover:border-violet-200 dark:bg-violet-500/10 dark:hover:border-violet-400/30',
    icon: 'bg-violet-100 text-violet-700 dark:bg-violet-500/20 dark:text-violet-200',
    badge:
      'border-violet-100 bg-white/70 text-violet-800 dark:border-violet-400/20 dark:bg-violet-500/10 dark:text-violet-200',
    dot: 'bg-violet-500',
  },
  {
    card: 'bg-sky-50/75 hover:border-sky-200 dark:bg-sky-500/10 dark:hover:border-sky-400/30',
    icon: 'bg-sky-100 text-sky-700 dark:bg-sky-500/20 dark:text-sky-200',
    badge:
      'border-sky-100 bg-white/70 text-sky-800 dark:border-sky-400/20 dark:bg-sky-500/10 dark:text-sky-200',
    dot: 'bg-sky-500',
  },
  {
    card: 'bg-emerald-50/75 hover:border-emerald-200 dark:bg-emerald-500/10 dark:hover:border-emerald-400/30',
    icon: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-200',
    badge:
      'border-emerald-100 bg-white/70 text-emerald-800 dark:border-emerald-400/20 dark:bg-emerald-500/10 dark:text-emerald-200',
    dot: 'bg-emerald-500',
  },
  {
    card: 'bg-amber-50/75 hover:border-amber-200 dark:bg-amber-500/10 dark:hover:border-amber-400/30',
    icon: 'bg-amber-100 text-amber-700 dark:bg-amber-500/20 dark:text-amber-200',
    badge:
      'border-amber-100 bg-white/70 text-amber-800 dark:border-amber-400/20 dark:bg-amber-500/10 dark:text-amber-200',
    dot: 'bg-amber-500',
  },
  {
    card: 'bg-rose-50/75 hover:border-rose-200 dark:bg-rose-500/10 dark:hover:border-rose-400/30',
    icon: 'bg-rose-100 text-rose-700 dark:bg-rose-500/20 dark:text-rose-200',
    badge:
      'border-rose-100 bg-white/70 text-rose-800 dark:border-rose-400/20 dark:bg-rose-500/10 dark:text-rose-200',
    dot: 'bg-rose-500',
  },
]

const credentialMetaClassName =
  'inline-flex max-w-[140px] items-center rounded border border-white/80 bg-white/55 px-1.5 py-0.5 text-[10.5px] font-semibold leading-none text-foreground shadow-sm dark:border-white/10 dark:bg-white/10'

function credentialCardClassName(tone: CredentialCardTone): string {
  return cn(
    'group relative flex min-h-[178px] cursor-pointer flex-col rounded-md border border-transparent p-4 text-left',
    'shadow-[0_10px_24px_-22px_rgba(15,23,42,0.45)] transition-all duration-150',
    'hover:-translate-y-px hover:shadow-[0_18px_32px_-24px_rgba(15,23,42,0.55)]',
    'focus-visible:-translate-y-px focus-visible:border-emerald-300 focus-visible:shadow-md',
    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/40',
    tone.card,
  )
}

function pickCredentialCardTone(seed: string): CredentialCardTone {
  let hash = 0
  for (let i = 0; i < seed.length; i += 1) hash += seed.charCodeAt(i)
  return CREDENTIAL_CARD_TONES[hash % CREDENTIAL_CARD_TONES.length]
}
