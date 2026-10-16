'use client'

import { useEffect, useId, useState, type KeyboardEvent } from 'react'
import { useTranslations } from 'next-intl'

import { Input } from '@/components/ui/input'
import { useActiveOrganization, usePrincipalSearch } from '@/lib/hooks/use-organizations'
import type { PrincipalSearchType, PrincipalSearchView } from '@/lib/types/organization'
import { cn } from '@/lib/utils'

import type { ShareSubject } from './share-backend'

const DEBOUNCE_MS = 250

export function principalKey(type: string, id: string): string {
  return `${type === 'org' ? 'organization' : type}:${id}`
}

export function toShareSubject(view: PrincipalSearchView): ShareSubject {
  return {
    type: view.type === 'org' ? 'organization' : view.type,
    id: view.id,
    displayName: view.display_name,
  }
}

interface PrincipalPickerProps {
  types: readonly PrincipalSearchType[]
  /** `principalKey()` values already holding access; hidden from the results. */
  excludeKeys?: ReadonlySet<string>
  label: string
  disabled?: boolean
  onPick: (subject: ShareSubject) => void
}

/** Org-scoped user / group / organization search as an ARIA 1.2 combobox (plan 13.3). */
export function PrincipalPicker({
  types,
  excludeKeys,
  label,
  disabled,
  onPick,
}: PrincipalPickerProps) {
  const t = useTranslations('sharing.picker')
  const listId = useId()
  const hintId = useId()
  const { activeOrganization } = useActiveOrganization()
  const [query, setQuery] = useState('')
  const [debounced, setDebounced] = useState('')
  const [active, setActive] = useState(0)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(query), DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [query])

  const search = usePrincipalSearch(activeOrganization?.id, debounced, types)
  const results = (search.data ?? []).filter(
    (view) => !excludeKeys?.has(principalKey(view.type, view.id)),
  )
  const tooShort = query.trim().length < 2
  const open = !tooShort && debounced === query && results.length > 0
  const activeIndex = Math.min(active, Math.max(results.length - 1, 0))

  function pick(view: PrincipalSearchView) {
    onPick(toShareSubject(view))
    setQuery('')
    setDebounced('')
    setActive(0)
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (!open) return
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      const step = event.key === 'ArrowDown' ? 1 : -1
      setActive((activeIndex + step + results.length) % results.length)
    } else if (event.key === 'Enter') {
      event.preventDefault()
      pick(results[activeIndex])
    } else if (event.key === 'Escape') {
      // Close the list only; the dialog stays open.
      event.preventDefault()
      event.stopPropagation()
      setQuery('')
      setDebounced('')
    }
  }

  const status = !activeOrganization
    ? t('noOrganization')
    : tooShort
      ? t('hint')
      : search.isFetching || debounced !== query
        ? t('searching')
        : search.isError
          ? t('error')
          : results.length === 0
            ? t('empty')
            : t('count', { count: results.length })

  return (
    <div className="relative space-y-1.5">
      <Input
        role="combobox"
        aria-label={label}
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-describedby={hintId}
        aria-activedescendant={open ? `${listId}-${activeIndex}` : undefined}
        value={query}
        disabled={disabled || !activeOrganization}
        placeholder={t('placeholder')}
        onChange={(event) => {
          setQuery(event.target.value)
          setActive(0)
        }}
        onKeyDown={onKeyDown}
      />
      <p id={hintId} className="text-xs text-muted-foreground" aria-live="polite">
        {status}
      </p>
      <ul
        id={listId}
        role="listbox"
        aria-label={label}
        hidden={!open}
        className="max-h-56 divide-y divide-border overflow-y-auto rounded-md border border-border bg-popover"
      >
        {results.map((view, index) => (
          <li
            key={principalKey(view.type, view.id)}
            id={`${listId}-${index}`}
            role="option"
            aria-selected={index === activeIndex}
            className={cn(
              'flex min-h-10 cursor-pointer items-center justify-between gap-3 px-3 py-2 text-sm',
              index === activeIndex && 'bg-accent',
            )}
            onMouseDown={(event) => event.preventDefault()}
            onMouseEnter={() => setActive(index)}
            onClick={() => pick(view)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') pick(view)
            }}
          >
            <span className="min-w-0 truncate">{view.display_name}</span>
            <span className="shrink-0 text-xs text-muted-foreground">
              {view.type === 'group' && view.member_count !== null
                ? t('groupMembers', { count: view.member_count })
                : (view.email ?? t(`type.${view.type}`))}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}
