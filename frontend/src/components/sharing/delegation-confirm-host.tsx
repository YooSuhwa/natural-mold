'use client'

import { useEffect, useState } from 'react'
import { useTranslations } from 'next-intl'
import { UsersIcon } from 'lucide-react'

import { DialogShell } from '@/components/shared/dialog-shell'
import {
  subscribeDelegationConfirm,
  type DelegationConfirmRequest,
} from '@/lib/sharing/delegation-confirm-request'

import { DelegationConfirm } from './delegation-confirm'

/**
 * D17 (E-06): asks before an agent settings save lends new accounts to the people the
 * agent is shared with (`409 confirm_required` on `PUT /api/agents/{id}`). Mounted once
 * in the root layout; `useUpdateAgent` resends with `confirm_delegation` on confirm.
 */
export function DelegationConfirmHost() {
  const t = useTranslations('sharing.delegation')
  const [request, setRequest] = useState<DelegationConfirmRequest | null>(null)

  useEffect(() => subscribeDelegationConfirm(setRequest), [])

  function settle(confirmed: boolean) {
    request?.resolve(confirmed)
    setRequest(null)
  }

  return (
    <DialogShell
      open={request !== null}
      onOpenChange={(open) => (open ? undefined : settle(false))}
      size="md"
      height="auto"
    >
      <DialogShell.Header
        icon={<UsersIcon className="size-5" />}
        title={t('saveTitle')}
        description={t('saveDescription')}
      />
      {request ? (
        <DialogShell.Body>
          <DelegationConfirm
            delegations={request.delegations}
            pending={false}
            confirmLabel={t('confirmSave')}
            onConfirm={() => settle(true)}
            onCancel={() => settle(false)}
          />
        </DialogShell.Body>
      ) : null}
    </DialogShell>
  )
}
