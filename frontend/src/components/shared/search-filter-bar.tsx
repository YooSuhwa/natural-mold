'use client'

import type { ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { SearchInput } from '@/components/shared/search-input'
import { cn } from '@/lib/utils'

interface SearchFilterBarProps {
  value: string
  onValueChange: (value: string) => void
  searchLabel: string
  placeholder?: string
  resetLabel?: string
  onReset?: () => void
  filters?: ReactNode
  actions?: ReactNode
  className?: string
}

export function SearchFilterBar({
  value,
  onValueChange,
  searchLabel,
  placeholder,
  resetLabel,
  onReset,
  filters,
  actions,
  className,
}: SearchFilterBarProps) {
  return (
    <div
      className={cn(
        'flex flex-col gap-3 md:flex-row md:items-center md:justify-between',
        className,
      )}
    >
      <div className="flex min-w-0 flex-1 flex-col gap-2 sm:flex-row sm:items-center">
        <SearchInput
          value={value}
          onChange={(event) => onValueChange(event.currentTarget.value)}
          aria-label={searchLabel}
          placeholder={placeholder}
          containerClassName="min-w-0 flex-1"
        />
        {filters}
        {onReset && resetLabel ? (
          <Button type="button" variant="ghost" size="sm" onClick={onReset}>
            {resetLabel}
          </Button>
        ) : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  )
}
