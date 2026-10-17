'use client'

import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { ChevronLeftIcon } from 'lucide-react'

import { ApiErrorState } from '@/components/shared/api-error-state'
import { ApiError } from '@/lib/api/client'

const MISSING = new ApiError(404, 'marketplace_item_not_found', '')

/** Load failure of the item detail through the shared error display (plan 13.1). */
export function MarketplaceDetailError({ error }: { error: unknown }) {
  const t = useTranslations('marketplace.detail')
  return (
    <div className="moldy-app-surface flex flex-1 flex-col overflow-auto">
      <div className="mx-auto flex w-full max-w-7xl flex-1 flex-col gap-4 px-6 py-7 pb-20 md:px-8">
        <Link
          href="/marketplace"
          className="inline-flex items-center gap-1 text-sm text-primary-strong"
        >
          <ChevronLeftIcon className="size-4" />
          {t('back')}
        </Link>
        <ApiErrorState error={error ?? MISSING} />
      </div>
    </div>
  )
}
