'use client'

import type { ReactNode } from 'react'
import { useTranslations } from 'next-intl'
import { PlusIcon, Trash2Icon } from 'lucide-react'

import { useAgentAccess } from '@/components/agent/agent-access-context'
import { Button } from '@/components/ui/button'

// "+ 추가" and the row trash button edit the agent: read-only viewers see neither (13.9).

export function EditOnlyAddButton({ label, onClick }: { label: string; onClick: () => void }) {
  const { canEdit } = useAgentAccess()
  if (!canEdit) return null
  return (
    <Button size="sm" variant="ghost" onClick={onClick}>
      <PlusIcon className="size-3.5" />
      {label}
    </Button>
  )
}

interface RowProps {
  icon: ReactNode
  name: string
  onRemove: () => void
}

export function Row({ icon, name, onRemove }: RowProps) {
  const tc = useTranslations('common')
  const { canEdit } = useAgentAccess()
  return (
    <div className="flex items-center justify-between px-4 py-2">
      <div className="flex min-w-0 items-center gap-2">
        {icon}
        <span className="truncate text-sm">{name}</span>
      </div>
      {canEdit ? (
        <div className="flex shrink-0 items-center gap-1">
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={onRemove}
            aria-label={`${tc('remove')} ${name}`}
          >
            <Trash2Icon className="size-3.5" />
          </Button>
        </div>
      ) : null}
    </div>
  )
}
