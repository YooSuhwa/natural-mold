'use client'

import type { ReactNode } from 'react'
import { useTranslations } from 'next-intl'
import { UsersIcon } from 'lucide-react'

import { DialogShell } from '@/components/shared/dialog-shell'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useSession } from '@/lib/auth/session'
import type { ShareResourceType } from '@/lib/types/grant'

import { AccessList } from './access-list'
import { principalKey } from './principal-picker'
import { useResourceShareBackend, type ShareBackend } from './share-backend'
import { shareErrorMessage } from './share-error'
import { ShareForm } from './share-form'
import { TransferOwnershipSection } from './transfer-ownership-section'

interface ShareDialogFrameProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  resourceName: string
  /** Rendered once the dialog is open, so the backend only fetches while visible. */
  children: ReactNode
}

/** DialogShell chrome shared by every sharing surface (plan 13.3, 13.12.3). */
export function ShareDialogFrame({
  open,
  onOpenChange,
  resourceName,
  children,
}: ShareDialogFrameProps) {
  const t = useTranslations('sharing')
  return (
    // `fixed` (640px) height: the form, access list and transfer section scroll in one body.
    <DialogShell open={open} onOpenChange={onOpenChange} size="md" height="fixed">
      <DialogShell.Header
        icon={<UsersIcon className="size-5" />}
        title={t('title', { name: resourceName })}
        description={t('description')}
      />
      {open ? children : null}
    </DialogShell>
  )
}

interface ShareDialogContentProps {
  backend: ShareBackend
  resourceName: string
  notice?: ReactNode
  requiresAccountAck?: boolean
  /** Closes the dialog once the caller lost access (self-revoke, transfer away). */
  onClose: () => void
}

/** Picker + level form, current access list and ownership transfer over any backend. */
export function ShareDialogContent({
  backend,
  resourceName,
  notice,
  requiresAccountAck = false,
  onClose,
}: ShareDialogContentProps) {
  const t = useTranslations('sharing')
  const { data: user } = useSession()
  const currentUserId = user?.id ?? null
  const entries = backend.snapshot?.entries ?? []
  const owner = entries.find((entry) => entry.source === 'owner')
  const excludeKeys = new Set(
    entries
      .filter((entry) => entry.source !== 'governance')
      .map((entry) => principalKey(entry.principal.type, entry.principal.id)),
  )

  return (
    <DialogShell.Body>
      {backend.isLoading ? (
        <div className="space-y-2" aria-busy="true" aria-label={t('loading')}>
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-16 w-full" />
        </div>
      ) : backend.error || !backend.snapshot ? (
        <div role="alert" className="space-y-2 text-sm">
          <p>{shareErrorMessage(backend.error, t)}</p>
          <Button type="button" variant="outline" size="sm" onClick={backend.refetch}>
            {t('retry')}
          </Button>
        </div>
      ) : (
        <>
          {notice}
          {!backend.allowsAdd ? null : backend.snapshot.canShare ? (
            <ShareForm
              backend={backend}
              excludeKeys={excludeKeys}
              requiresAccountAck={requiresAccountAck}
            />
          ) : (
            <p className="text-sm text-muted-foreground">{t('readOnly')}</p>
          )}
          <section className="space-y-2" aria-label={t('access.title')}>
            <h3 className="text-xs font-medium text-muted-foreground">{t('access.title')}</h3>
            <AccessList backend={backend} currentUserId={currentUserId} onLostAccess={onClose} />
          </section>
          <TransferOwnershipSection
            backend={backend}
            resourceName={resourceName}
            isOwner={!!owner && owner.principal.id === currentUserId}
            ownerKey={owner ? principalKey('user', owner.principal.id) : null}
            onLostAccess={onClose}
          />
        </>
      )}
    </DialogShell.Body>
  )
}

function ResourceShareBody({
  resourceType,
  resourceId,
  resourceName,
  sharedAccount,
  onClose,
}: Omit<ResourceShareDialogProps, 'open' | 'onOpenChange'> & { onClose: () => void }) {
  const t = useTranslations('sharing.notice')
  const backend = useResourceShareBackend(resourceType, resourceId)
  const notice =
    resourceType === 'credential' ? (
      <p role="note" className="rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
        {t('credential')}
      </p>
    ) : null
  return (
    <ShareDialogContent
      backend={backend}
      resourceName={resourceName}
      notice={notice}
      onClose={onClose}
      requiresAccountAck={
        !!sharedAccount && (resourceType === 'tool' || resourceType === 'mcp_server')
      }
    />
  )
}

export interface ResourceShareDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  resourceType: ShareResourceType
  resourceId: string
  resourceName: string
  /** Tool / MCP server bound to its owner's credential (`shared` mode, 10.4). */
  sharedAccount?: boolean
}

/** Share any agent, skill, tool, MCP server or credential (plan 13.3, E-01 API). */
export function ResourceShareDialog({ open, onOpenChange, ...body }: ResourceShareDialogProps) {
  return (
    <ShareDialogFrame open={open} onOpenChange={onOpenChange} resourceName={body.resourceName}>
      <ResourceShareBody {...body} onClose={() => onOpenChange(false)} />
    </ShareDialogFrame>
  )
}
