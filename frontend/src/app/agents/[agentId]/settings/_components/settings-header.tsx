'use client'

import { useTranslations } from 'next-intl'
import { ArrowLeftIcon, Loader2Icon, Trash2Icon } from 'lucide-react'

import { AgentAvatar } from '@/components/agent/agent-avatar'
import { useAgentAccess } from '@/components/agent/agent-access-context'
import { ResourceShareButton } from '@/components/sharing/resource-share-button'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'

interface SettingsHeaderProps {
  agentId: string
  agentName: string
  imageUrl: string | null
  myPermissions?: readonly string[] | null
  name: string
  onNameChange: (value: string) => void
  description: string
  onDescriptionChange: (value: string) => void
  onBack: () => void
  onRequestDelete: () => void
  onSave: () => void
  saving: boolean
  saveDisabled: boolean
}

/** Settings header; edit controls follow the caller's access (plan 13.9 row 2). */
export function SettingsHeader({
  agentId,
  agentName,
  imageUrl,
  myPermissions,
  name,
  onNameChange,
  description,
  onDescriptionChange,
  onBack,
  onRequestDelete,
  onSave,
  saving,
  saveDisabled,
}: SettingsHeaderProps) {
  const t = useTranslations('agent.settings')
  const tc = useTranslations('common')
  const access = useAgentAccess()
  const readOnly = !access.canEdit

  return (
    <header className="moldy-panel-header flex items-start gap-3 px-6 py-3">
      <Button variant="ghost" size="icon-sm" onClick={onBack} aria-label={t('back')}>
        <ArrowLeftIcon className="size-4" />
      </Button>
      <AgentAvatar imageUrl={imageUrl} name={name} size="sm" />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <Input
          value={name}
          readOnly={readOnly}
          onChange={(e) => onNameChange(e.target.value)}
          className="h-8 rounded-md border-0 bg-transparent px-2 text-lg font-semibold shadow-none transition-colors hover:bg-muted/30 focus-visible:bg-muted/40 focus-visible:ring-0"
          placeholder={t('namePlaceholder')}
        />
        <Input
          value={description}
          readOnly={readOnly}
          onChange={(e) => onDescriptionChange(e.target.value)}
          className="h-7 rounded-md border-0 bg-transparent px-2 text-xs text-muted-foreground shadow-none transition-colors hover:bg-muted/30 focus-visible:bg-muted/40 focus-visible:ring-0"
          placeholder={t('descriptionPlaceholder')}
        />
        {readOnly ? (
          <p role="note" className="px-2 text-xs text-muted-foreground">
            {tc('readOnly')}
          </p>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {access.canShare ? (
          <ResourceShareButton
            resourceType="agent"
            resourceId={agentId}
            resourceName={agentName}
            myPermissions={myPermissions}
          />
        ) : null}
        {access.canDelete ? (
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={t('deleteAgent')}
            onClick={onRequestDelete}
          >
            <Trash2Icon className="size-4 text-destructive" />
          </Button>
        ) : null}
        {access.canEdit ? (
          <Button onClick={onSave} disabled={saveDisabled}>
            {saving ? <Loader2Icon className="mr-1 size-4 animate-spin" /> : null}
            {t('save')}
          </Button>
        ) : null}
      </div>
    </header>
  )
}

export function SettingsLoading() {
  return (
    <div className="flex flex-1 flex-col gap-6 overflow-auto p-6">
      <Skeleton className="h-6 w-40" />
      <div className="space-y-4">
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    </div>
  )
}
