'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { OctagonXIcon } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { useEmergencyStop } from '@/lib/hooks/use-approvals'
import type { StopScope } from '@/lib/types/approval'
import { StopReasonDialog } from './stop-reason-dialog'

/** 긴급 중지 (plan 17.4): blocks new runs, cancels running ones, pauses schedules. */
export function EmergencyStopButton({
  scope,
  targetId,
  targetName,
}: {
  scope: StopScope
  targetId: string
  targetName: string
}) {
  const t = useTranslations('emergencyStop')
  const stop = useEmergencyStop()
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
        <OctagonXIcon className="size-3.5 text-destructive" /> {t('button')}
      </Button>
      <StopReasonDialog
        open={open}
        onOpenChange={setOpen}
        icon={<OctagonXIcon className="size-5" />}
        title={t('title', { name: targetName })}
        description={t(`description.${scope}`)}
        confirmLabel={t('confirm')}
        pending={stop.isPending}
        onSubmit={(reason) =>
          stop.mutate(
            { scope, id: targetId, reason },
            {
              onSuccess: () => {
                toast.success(t('stopped'))
                setOpen(false)
              },
              onError: () => toast.error(t('failed')),
            },
          )
        }
      />
    </>
  )
}
