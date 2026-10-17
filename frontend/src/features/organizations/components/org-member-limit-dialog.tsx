'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { ApiErrorState } from '@/components/shared/api-error-state'
import { DialogShell } from '@/components/shared/dialog-shell'
import { FormFieldShell } from '@/components/shared/form-field-shell'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { useOrgUserLimit, useSaveUserLimit } from '@/lib/hooks/use-org-governance'
import type { UsageLimitView } from '@/lib/types/org-governance'
import type { MemberView } from '@/lib/types/organization'
import { isAmount, toAmount, usd } from '../lib/amount'
import { useOrgErrorMessage } from '../lib/error-message'

interface MemberLimitDialogProps {
  orgId: string
  member: MemberView
  /** Organization version (a user limit is an organization policy). */
  version: string
  onClose: () => void
}

/** S10 "월 한도 지정": the member's monthly limit in this organization (empty = org default). */
export function MemberLimitDialog({ orgId, member, version, onClose }: MemberLimitDialogProps) {
  const t = useTranslations('orgAdmin.members.limit')
  const limit = useOrgUserLimit(orgId, member.user_id)
  return (
    <DialogShell
      open
      onOpenChange={(open) => (open ? undefined : onClose())}
      size="sm"
      height="auto"
    >
      <DialogShell.Header
        title={t('title', { name: member.display_name })}
        description={t('description')}
      />
      {limit.isLoading ? (
        <DialogShell.Body>
          <Skeleton className="h-16 w-full" />
        </DialogShell.Body>
      ) : limit.data ? (
        <LimitForm
          orgId={orgId}
          member={member}
          view={limit.data}
          version={version}
          onClose={onClose}
        />
      ) : (
        <DialogShell.Body>
          <ApiErrorState error={limit.error} onRetry={() => void limit.refetch()} />
        </DialogShell.Body>
      )}
    </DialogShell>
  )
}

function LimitForm({
  orgId,
  member,
  view,
  version,
  onClose,
}: MemberLimitDialogProps & { view: UsageLimitView }) {
  const t = useTranslations('orgAdmin.members.limit')
  const tCommon = useTranslations('orgAdmin.common')
  const errorMessage = useOrgErrorMessage()
  const save = useSaveUserLimit(orgId)
  const [value, setValue] = useState(view.monthly_spend_limit ?? '')
  const amount = toAmount(value)
  const overCap =
    amount !== null && view.parent_limit !== null && Number(amount) > Number(view.parent_limit)
  const invalid = (value.trim() !== '' && !isAmount(value)) || overCap

  return (
    <>
      <DialogShell.Body>
        <div className="space-y-3">
          <p className="text-sm">
            {t('usage', {
              used: usd(view.used) ?? '',
              limit: usd(view.effective_limit) ?? t('noLimit'),
            })}
          </p>
          <FormFieldShell
            id="member-limit"
            label={t('label')}
            description={
              view.parent_limit ? t('cap', { cap: usd(view.parent_limit) ?? '' }) : t('hint')
            }
            error={overCap ? t('overCap') : invalid ? t('invalid') : null}
          >
            <Input
              id="member-limit"
              inputMode="decimal"
              placeholder={t('placeholder')}
              value={value}
              onChange={(event) => setValue(event.target.value)}
            />
          </FormFieldShell>
        </div>
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button variant="outline" onClick={onClose}>
          {tCommon('cancel')}
        </Button>
        <Button
          disabled={invalid || save.isPending}
          onClick={() =>
            save.mutate(
              { userId: member.user_id, limit: amount, version },
              {
                onSuccess: () => {
                  toast.success(t('saved'))
                  onClose()
                },
                onError: (error) => toast.error(errorMessage(error)),
              },
            )
          }
        >
          {tCommon('save')}
        </Button>
      </DialogShell.Footer>
    </>
  )
}
