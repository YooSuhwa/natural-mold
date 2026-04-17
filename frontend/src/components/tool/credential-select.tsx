'use client'

import { PlusIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
  SelectSeparator,
} from '@/components/ui/select'
import type { Credential } from '@/lib/types'

export const CREDENTIAL_NONE = 'none'
export const CREDENTIAL_CREATE = '__create__'

interface CredentialSelectProps {
  value: string
  onValueChange: (value: string) => void
  onCreateRequested: () => void
  credentials: Credential[]
  noneLabel?: string
}

/**
 * Shared credential picker used by add-tool / mcp-auth / prebuilt-auth dialogs.
 * Emits a special `CREDENTIAL_CREATE` sentinel to ask the caller to open a
 * nested creation dialog; the caller is responsible for rendering that.
 */
export function CredentialSelect({
  value,
  onValueChange,
  onCreateRequested,
  credentials,
  noneLabel,
}: CredentialSelectProps) {
  const tCred = useTranslations('connections.credentialSelect')
  const resolvedNoneLabel = noneLabel ?? tCred('none')

  function handleChange(v: string | null) {
    if (!v) return
    if (v === CREDENTIAL_CREATE) {
      onCreateRequested()
      return
    }
    onValueChange(v)
  }

  return (
    <Select value={value} onValueChange={handleChange}>
      <SelectTrigger className="w-full">
        <SelectValue placeholder={tCred('placeholder')}>
          {(v: string) => {
            if (v === CREDENTIAL_NONE) return resolvedNoneLabel
            const cred = credentials.find((c) => c.id === v)
            return cred?.name ?? ''
          }}
        </SelectValue>
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={CREDENTIAL_NONE}>{resolvedNoneLabel}</SelectItem>
        {credentials.length > 0 && <SelectSeparator />}
        {credentials.map((c) => (
          <SelectItem key={c.id} value={c.id}>
            {c.name}
          </SelectItem>
        ))}
        <SelectSeparator />
        <SelectItem value={CREDENTIAL_CREATE}>
          <span className="flex items-center gap-1.5">
            <PlusIcon className="size-3.5" />
            {tCred('createNew')}
          </span>
        </SelectItem>
      </SelectContent>
    </Select>
  )
}
