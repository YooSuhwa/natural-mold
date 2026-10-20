'use client'

import { useMemo } from 'react'
import { useTranslations } from 'next-intl'

import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useCompanySharedCredentials, useCredentials } from '@/lib/hooks/use-credentials'
import { StatusChip } from '@/components/shared/status-chip'

interface CredentialPickerProps {
  /** Restrict the picker to credentials matching one of these definition keys. */
  definitionKeys?: string[]
  value?: string | null
  onChange: (id: string | null) => void
  disabled?: boolean
  placeholder?: string
  /** Allow `(none)` selection. */
  clearable?: boolean
  /** Also offer the company shared keys designated to my organization (tool/MCP shared accounts). */
  includeCompany?: boolean
}

export function CredentialPicker({
  definitionKeys,
  value,
  onChange,
  disabled,
  placeholder,
  clearable = true,
  includeCompany = false,
}: CredentialPickerProps) {
  const t = useTranslations('credentials.picker')
  const { data: credentials, isLoading } = useCredentials()
  const { data: companyKeys } = useCompanySharedCredentials(includeCompany)
  const placeholderText = placeholder ?? t('placeholder')

  const filtered = useMemo(() => {
    if (!credentials) return []
    if (!definitionKeys?.length) return credentials
    return credentials.filter((c) => definitionKeys.includes(c.definition_key))
  }, [credentials, definitionKeys])
  const company = useMemo(() => {
    if (!includeCompany || !companyKeys) return []
    if (!definitionKeys?.length) return companyKeys
    return companyKeys.filter((c) => definitionKeys.includes(c.definition_key))
  }, [includeCompany, companyKeys, definitionKeys])

  return (
    <Select
      value={value ?? '__none__'}
      onValueChange={(v) => onChange(v === '__none__' ? null : v)}
      disabled={disabled || isLoading}
    >
      <SelectTrigger className="w-full">
        {/* base-ui Select can't auto-extract a label from JSX children of
            SelectItem, so it falls back to the raw value (UUID). The
            function-children form receives the current value and lets us
            render the correct human label ourselves. */}
        <SelectValue placeholder={placeholderText}>
          {(selected) => {
            if (!selected || selected === '__none__') return placeholderText
            const match =
              filtered.find((c) => c.id === selected) ?? company.find((c) => c.id === selected)
            return match ? match.name : placeholderText
          }}
        </SelectValue>
      </SelectTrigger>
      <SelectContent>
        {clearable && <SelectItem value="__none__">{t('none')}</SelectItem>}
        {company.length ? (
          <>
            <SelectGroup>
              <SelectLabel>{t('ownGroup')}</SelectLabel>
              {filtered.map((c) => (
                <SelectItem key={c.id} value={c.id}>
                  <span className="flex items-center gap-2">
                    <span>{c.name}</span>
                    <StatusChip variant={c.status} className="ml-1" />
                  </span>
                </SelectItem>
              ))}
            </SelectGroup>
            <SelectGroup>
              <SelectLabel>{t('companyGroup')}</SelectLabel>
              {company.map((c) => (
                <SelectItem key={c.id} value={c.id}>
                  {c.name}
                </SelectItem>
              ))}
            </SelectGroup>
          </>
        ) : (
          filtered.map((c) => (
            <SelectItem key={c.id} value={c.id}>
              <span className="flex items-center gap-2">
                <span>{c.name}</span>
                <StatusChip variant={c.status} className="ml-1" />
              </span>
            </SelectItem>
          ))
        )}
      </SelectContent>
    </Select>
  )
}
