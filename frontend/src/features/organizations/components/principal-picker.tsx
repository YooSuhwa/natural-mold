'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { usePrincipalSearch } from '@/lib/hooks/use-organizations'
import type { PrincipalSearchType, PrincipalSearchView } from '@/lib/types/organization'

interface PrincipalPickerProps {
  orgId: string
  types: readonly PrincipalSearchType[]
  actionLabel: string
  disabled?: boolean
  onPick: (principal: PrincipalSearchView) => void
}

export function PrincipalPicker({
  orgId,
  types,
  actionLabel,
  disabled,
  onPick,
}: PrincipalPickerProps) {
  const t = useTranslations('orgAdmin.picker')
  const [query, setQuery] = useState('')
  const search = usePrincipalSearch(orgId, query, types)
  const results = search.data ?? []
  const showHint = query.trim().length < 2

  return (
    <div className="space-y-2">
      <Input
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder={t('placeholder')}
        aria-label={t('placeholder')}
      />
      {showHint ? (
        <p className="text-xs text-muted-foreground">{t('hint')}</p>
      ) : search.isLoading ? (
        <p className="text-xs text-muted-foreground">{t('searching')}</p>
      ) : results.length === 0 ? (
        <p className="text-xs text-muted-foreground">{t('empty')}</p>
      ) : (
        <ul className="divide-y divide-border rounded-md border border-border">
          {results.map((principal) => (
            <li
              key={`${principal.type}:${principal.id}`}
              className="flex items-center justify-between gap-3 px-3 py-2"
            >
              <span className="min-w-0">
                <span className="block truncate text-sm">{principal.display_name}</span>
                <span className="block truncate text-xs text-muted-foreground">
                  {principal.email ?? t(`type.${principal.type}`)}
                </span>
              </span>
              <Button
                size="sm"
                variant="outline"
                disabled={disabled}
                onClick={() => {
                  onPick(principal)
                  setQuery('')
                }}
              >
                {actionLabel}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
