'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useFormatter, useTranslations } from 'next-intl'

import { ApiErrorState } from '@/components/shared/api-error-state'
import { PageHeader } from '@/components/shared/page-header'
import { SearchInput } from '@/components/shared/search-input'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { DirectoryPager } from '@/features/organizations/components/directory-pager'
import { useCursorPager } from '@/features/organizations/lib/cursor-pager'
import { usePlatformTenants } from '@/lib/hooks/use-platform-tenants'
import type { PlatformTenant, PlatformTenantStatus } from '@/lib/types/platform-tenant'
import { formatMoney, toAmount } from '../lib/messages'
import { useDebouncedValue } from '../lib/use-debounced-value'
import { TenantActionsMenu } from './tenant-actions-menu'
import { TenantCreateDialog } from './tenant-create-dialog'
import { TenantStatusChip } from './tenant-status-chip'

export const SEARCH_DEBOUNCE_MS = 250

const FILTERS: readonly (PlatformTenantStatus | null)[] = [
  null,
  'active',
  'suspended',
  'deletion_scheduled',
]

/** S03 플랫폼 운영: 회사 목록 (plan 13.14). Metadata only — no company content. */
export function TenantListContent() {
  const t = useTranslations('appSettings.platformTenants.list')
  const tStatus = useTranslations('appSettings.platformTenants.status')
  const pager = useCursorPager()
  const [status, setStatus] = useState<PlatformTenantStatus | null>(null)
  const [search, setSearch] = useState('')
  const [creating, setCreating] = useState(false)
  const debouncedSearch = useDebouncedValue(search.trim(), SEARCH_DEBOUNCE_MS)
  const query = usePlatformTenants(status, debouncedSearch, pager.cursor)
  const data = query.data

  return (
    <div className="space-y-4">
      <PageHeader
        title={t('title')}
        description={t('description')}
        action={<Button onClick={() => setCreating(true)}>{t('create')}</Button>}
      />
      <SettingsSectionCard title={t('sectionTitle')}>
        <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div role="group" aria-label={t('filterLabel')} className="flex flex-wrap gap-2">
            {FILTERS.map((option) => (
              <Button
                key={option ?? 'all'}
                size="sm"
                variant={status === option ? 'secondary' : 'outline'}
                aria-pressed={status === option}
                onClick={() => {
                  setStatus(option)
                  pager.reset()
                }}
              >
                {option ? tStatus(option) : t('all')}
              </Button>
            ))}
          </div>
          <SearchInput
            aria-label={t('searchLabel')}
            placeholder={t('searchPlaceholder')}
            value={search}
            maxLength={200}
            containerClassName="sm:w-64"
            onChange={(event) => {
              setSearch(event.target.value)
              pager.reset()
            }}
          />
        </div>
        {query.isPending ? (
          <Skeleton className="h-32 w-full" data-testid="tenant-list-loading" />
        ) : query.isError ? (
          <ApiErrorState error={query.error} onRetry={() => void query.refetch()} />
        ) : data && data.items.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('empty')}</p>
        ) : data ? (
          <TenantTable items={data.items} />
        ) : null}
        <DirectoryPager
          canGoBack={pager.canGoBack}
          hasMore={data?.has_more ?? false}
          onBack={pager.back}
          onNext={() => pager.next(data?.next_cursor ?? null)}
        />
      </SettingsSectionCard>
      {creating ? <TenantCreateDialog onClose={() => setCreating(false)} /> : null}
    </div>
  )
}

function TenantTable({ items }: { items: PlatformTenant[] }) {
  const t = useTranslations('appSettings.platformTenants.list')
  const format = useFormatter()
  const limitText = (value: number | null) => (value === null ? t('noLimit') : format.number(value))
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead scope="col">{t('company')}</TableHead>
          <TableHead scope="col">{t('organizations')}</TableHead>
          <TableHead scope="col">{t('members')}</TableHead>
          <TableHead scope="col">{t('spendLimit')}</TableHead>
          <TableHead scope="col">{t('status')}</TableHead>
          <TableHead scope="col">
            <span className="sr-only">{t('actions')}</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((tenant) => (
          <TableRow key={tenant.id}>
            <TableCell>
              <Link
                href={`/settings/admin/tenants/${encodeURIComponent(tenant.id)}`}
                className="block text-sm font-medium text-foreground hover:underline"
              >
                {tenant.name}
              </Link>
              <span className="block text-xs text-muted-foreground">{tenant.slug}</span>
            </TableCell>
            <TableCell>{format.number(tenant.organization_count)}</TableCell>
            <TableCell>
              {t('ofLimit', {
                used: format.number(tenant.active_member_count),
                limit: limitText(tenant.limits.member_limit),
              })}
            </TableCell>
            <TableCell>
              {formatMoney(toAmount(tenant.limits.monthly_spend_limit), t('noLimit'))}
            </TableCell>
            <TableCell>
              <TenantStatusChip status={tenant.status} />
            </TableCell>
            <TableCell className="text-right">
              <TenantActionsMenu tenant={tenant} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
