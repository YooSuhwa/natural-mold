'use client'

import { ChevronLeftIcon, ChevronRightIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'

interface DirectoryPagerProps {
  canGoBack: boolean
  hasMore: boolean
  onBack: () => void
  onNext: () => void
}

export function DirectoryPager({ canGoBack, hasMore, onBack, onNext }: DirectoryPagerProps) {
  const t = useTranslations('orgAdmin.common')
  if (!canGoBack && !hasMore) return null
  return (
    <div className="flex items-center justify-end gap-2 pt-3">
      <Button variant="outline" size="sm" disabled={!canGoBack} onClick={onBack}>
        <ChevronLeftIcon />
        {t('previous')}
      </Button>
      <Button variant="outline" size="sm" disabled={!hasMore} onClick={onNext}>
        {t('next')}
        <ChevronRightIcon />
      </Button>
    </div>
  )
}
