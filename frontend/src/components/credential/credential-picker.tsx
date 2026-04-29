'use client'

import { useMemo } from 'react'

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useCredentials } from '@/lib/hooks/use-credentials'
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
}

export function CredentialPicker({
  definitionKeys,
  value,
  onChange,
  disabled,
  placeholder = 'Select a credential',
  clearable = true,
}: CredentialPickerProps) {
  const { data: credentials, isLoading } = useCredentials()

  const filtered = useMemo(() => {
    if (!credentials) return []
    if (!definitionKeys?.length) return credentials
    return credentials.filter((c) => definitionKeys.includes(c.definition_key))
  }, [credentials, definitionKeys])

  return (
    <Select
      value={value ?? '__none__'}
      onValueChange={(v) => onChange(v === '__none__' ? null : v)}
      disabled={disabled || isLoading}
    >
      <SelectTrigger className="w-full">
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {clearable && <SelectItem value="__none__">(no credential)</SelectItem>}
        {filtered.map((c) => (
          <SelectItem key={c.id} value={c.id}>
            <span className="flex items-center gap-2">
              <span>{c.name}</span>
              <StatusChip variant={c.status} className="ml-1" />
            </span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
