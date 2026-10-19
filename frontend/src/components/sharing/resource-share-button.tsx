'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { UsersIcon } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useSession } from '@/lib/auth/session'
import { cn } from '@/lib/utils'

import { ResourceShareDialog, type ResourceShareDialogProps } from './resource-share-dialog'
import { canShareResource, isSharedWithMe } from './share-permissions'

type ResourceShareButtonProps = Omit<ResourceShareDialogProps, 'open' | 'onOpenChange'> & {
  className?: string
  /** Icon-only trigger for dense cards (the accessible name keeps the resource name). */
  compact?: boolean
  /** Row owner (legacy gate: only the owner shares). */
  ownerId?: string | null
  /** Shadow/enforce `my_permissions`, when the row carries them. */
  myPermissions?: readonly string[] | null
}

/** Opens `ResourceShareDialog`; hidden unless the caller may share (plan 13.4, 13.12). */
export function ResourceShareButton({
  className,
  compact,
  ownerId,
  myPermissions,
  ...dialog
}: ResourceShareButtonProps) {
  const t = useTranslations('sharing')
  const { data: user } = useSession()
  const [open, setOpen] = useState(false)
  if (!canShareResource({ myPermissions, ownerId, currentUserId: user?.id })) return null
  return (
    <>
      <Button
        type="button"
        variant="ghost"
        size={compact ? 'icon-sm' : 'sm'}
        className={cn(!compact && 'h-7 px-2 text-xs', className)}
        aria-label={t('open', { name: dialog.resourceName })}
        onClick={() => setOpen(true)}
      >
        <UsersIcon className="size-3.5" />
        {compact ? null : t('share')}
      </Button>
      <ResourceShareDialog {...dialog} open={open} onOpenChange={setOpen} />
    </>
  )
}

/** "공유받음" marker for rows owned by someone else (plan 13.4); text, not color only. */
export function SharedWithMeBadge({
  ownerId,
  className,
}: {
  ownerId: string | null | undefined
  className?: string
}) {
  const t = useTranslations('sharing')
  const { data: user } = useSession()
  if (!isSharedWithMe(ownerId, user?.id)) return null
  return (
    <Badge variant="outline" className={cn('gap-1', className)}>
      <UsersIcon className="size-3" aria-hidden="true" />
      {t('sharedWithMe')}
    </Badge>
  )
}
