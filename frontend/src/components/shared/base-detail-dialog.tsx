'use client'

import { useState, type ReactNode } from 'react'
import { Loader2Icon } from 'lucide-react'
import type { UseMutationResult, UseQueryResult } from '@tanstack/react-query'

import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { DialogShell } from '@/components/shared/dialog-shell'
import { DeleteConfirmInline } from '@/components/shared/delete-confirm-inline'
import { ErrorState } from '@/components/shared/error-state'
import type { DialogSize } from '@/lib/design-tokens'

interface Props<T> {
  id: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
  size?: DialogSize
  entityLabel: string
  query: UseQueryResult<T | undefined>
  remove?: UseMutationResult<unknown, unknown, string>
  icon?: ReactNode
  renderTitle: (entity: T) => ReactNode
  renderDescription?: (entity: T) => ReactNode
  renderActions?: (entity: T) => ReactNode
  renderBody: (entity: T) => ReactNode
  extraFooter?: (entity: T) => ReactNode
}

export function BaseDetailDialog<T>(props: Props<T>) {
  // Re-mount inner when id changes so internal state resets cleanly.
  return <BaseDetailDialogInner key={props.id ?? 'closed'} {...props} />
}

function BaseDetailDialogInner<T>({
  id,
  open,
  onOpenChange,
  size = 'lg',
  entityLabel,
  query,
  remove,
  icon,
  renderTitle,
  renderDescription,
  renderActions,
  renderBody,
  extraFooter,
}: Props<T>) {
  const [confirming, setConfirming] = useState(false)
  const entity = query.data
  const handleClose = (next: boolean) => {
    if (!next) setConfirming(false)
    onOpenChange(next)
  }

  return (
    <DialogShell open={open} onOpenChange={handleClose} size={size}>
      {query.isLoading ? (
        <>
          <DialogShell.Header title={`Loading ${entityLabel}…`} />
          <DialogShell.Body>
            <Skeleton className="h-40 w-full rounded-lg" />
            <Skeleton className="h-32 w-full rounded-lg" />
          </DialogShell.Body>
          <DialogShell.Footer>
            <Loader2Icon className="size-4 animate-spin text-muted-foreground" />
          </DialogShell.Footer>
        </>
      ) : query.isError || !entity ? (
        <>
          <DialogShell.Header title={`Failed to load ${entityLabel}`} />
          <DialogShell.Body>
            <ErrorState onRetry={() => void query.refetch()} />
          </DialogShell.Body>
          <DialogShell.Footer>
            <Button variant="outline" onClick={() => handleClose(false)}>
              Close
            </Button>
          </DialogShell.Footer>
        </>
      ) : (
        <>
          <DialogShell.Header
            icon={icon}
            title={renderTitle(entity)}
            description={renderDescription?.(entity)}
            actions={renderActions?.(entity)}
          />
          <DialogShell.Body>{renderBody(entity)}</DialogShell.Body>
          <DialogShell.Footer>
            {remove && id ? (
              confirming ? (
                <div className="flex-1">
                  <DeleteConfirmInline
                    entity={entityLabel}
                    onCancel={() => setConfirming(false)}
                    onConfirm={() =>
                      remove.mutate(id, {
                        onSuccess: () => handleClose(false),
                      })
                    }
                    pending={remove.isPending}
                  />
                </div>
              ) : (
                <Button
                  variant="ghost"
                  size="sm"
                  className="mr-auto text-destructive hover:bg-destructive/10 hover:text-destructive"
                  onClick={() => setConfirming(true)}
                >
                  Delete
                </Button>
              )
            ) : null}
            {extraFooter?.(entity)}
            <Button variant="outline" onClick={() => handleClose(false)}>
              Close
            </Button>
          </DialogShell.Footer>
        </>
      )}
    </DialogShell>
  )
}
