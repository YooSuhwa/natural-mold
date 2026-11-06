'use client'

import { useTranslations } from 'next-intl'
import { PlayIcon } from 'lucide-react'
import { toast } from 'sonner'

import { StopReasonDialog } from '@/features/approvals/components/stop-reason-dialog'
import { ApiError } from '@/lib/api/client'
import { useReleaseEmergencyStop } from '@/lib/hooks/use-approvals'
import type { EmergencyStopView } from '@/lib/types/approval'

export function ReleaseStopDialog({
  stop,
  onClose,
}: {
  stop: EmergencyStopView | null
  onClose: () => void
}) {
  const t = useTranslations('approvals.stops')
  const release = useReleaseEmergencyStop()
  return (
    <StopReasonDialog
      open={stop !== null}
      onOpenChange={(open) => (open ? null : onClose())}
      icon={<PlayIcon className="size-5" />}
      title={t('releaseTitle', { name: stop?.scope_name ?? '' })}
      description={t('releaseDescription')}
      confirmLabel={t('release')}
      pending={release.isPending}
      onSubmit={(reason) =>
        stop &&
        release.mutate(
          { id: stop.id, reason },
          {
            onSuccess: () => {
              toast.success(t('released'))
              onClose()
            },
            onError: (error) =>
              toast.error(
                error instanceof ApiError && error.code === 'emergency_stop_release_forbidden'
                  ? t('releaseForbidden')
                  : t('releaseFailed'),
              ),
          },
        )
      }
    />
  )
}
