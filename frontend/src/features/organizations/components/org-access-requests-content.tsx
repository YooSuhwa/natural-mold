'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { LineTabsList, LineTabsTrigger } from '@/components/ui/line-tabs'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs } from '@/components/ui/tabs'
import { ApiError } from '@/lib/api/errors'
import {
  useAccessRequests,
  useDecideAccessRequest,
  type AccessDecision,
} from '@/lib/hooks/use-access-requests'
import type { AccessRequestView, RequestBox } from '@/lib/types/access-request'
import { useCursorPager } from '../lib/cursor-pager'
import { DirectoryPager } from './directory-pager'
import { OrgPageFrame } from './org-page-frame'

const BOXES: readonly RequestBox[] = ['inbox', 'outbox', 'handled']
const DAY_MS = 86_400_000

/** S18 요청함: received (approve/deny), sent (cancel) and handled requests (plan 10.9). */
export function OrgAccessRequestsContent() {
  const t = useTranslations('orgAdmin.requests')
  return (
    <OrgPageFrame title={t('title')} description={t('description')}>
      {() => <RequestsBody />}
    </OrgPageFrame>
  )
}

function RequestsBody() {
  const t = useTranslations('orgAdmin.requests')
  const [box, setBox] = useState<RequestBox>('inbox')
  const pager = useCursorPager()
  const page = useAccessRequests(box, pager.cursor)

  const select = (next: RequestBox) => {
    setBox(next)
    pager.reset()
  }

  return (
    <SettingsSectionCard title={t(`tabs.${box}`)}>
      <Tabs value={box} onValueChange={(value) => select(value as RequestBox)} className="mb-3">
        <LineTabsList aria-label={t('title')}>
          {BOXES.map((value) => (
            <LineTabsTrigger key={value} value={value}>
              {t(`tabs.${value}`)}
            </LineTabsTrigger>
          ))}
        </LineTabsList>
      </Tabs>
      {page.isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : !page.data ? (
        <p className="text-sm text-status-danger">{t('loadFailed')}</p>
      ) : page.data.items.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t(`empty.${box}`)}</p>
      ) : (
        <ul className="divide-y divide-border">
          {page.data.items.map((request) => (
            <RequestRow key={request.id} request={request} box={box} />
          ))}
        </ul>
      )}
      <DirectoryPager
        canGoBack={pager.canGoBack}
        hasMore={page.data?.has_more ?? false}
        onBack={pager.back}
        onNext={() => pager.next(page.data?.next_cursor ?? null)}
      />
    </SettingsSectionCard>
  )
}

function RequestRow({ request, box }: { request: AccessRequestView; box: RequestBox }) {
  const t = useTranslations('orgAdmin.requests')
  const tLevel = useTranslations('sharing.level')
  const decide = useDecideAccessRequest()
  const pending = request.status === 'pending'
  const [now] = useState(() => Date.now()) // render-stable "today" for the expiry countdown
  const days = Math.max(0, Math.ceil((Date.parse(request.expires_at) - now) / DAY_MS))

  const run = (decision: AccessDecision, done: 'approved' | 'denied' | 'cancelled') =>
    decide.mutate(decision, {
      onSuccess: () => toast.success(t(`toast.${done}`)),
      onError: (error) => {
        const code = error instanceof ApiError ? error.code : ''
        const known = code === 'access_request_closed' || code === 'confirm_required'
        toast.error(t(known ? `errors.${code}` : 'errors.generic'))
      },
    })

  return (
    <li className="flex flex-wrap items-start justify-between gap-3 py-3">
      <div className="min-w-0 space-y-1">
        <p className="text-sm font-medium">
          {t('row', {
            requester: request.requester.display_name ?? t('unknownUser'),
            resource: request.resource_name ?? t('unknownResource'),
            level: tLevel(request.level),
          })}
        </p>
        {request.reason ? (
          <p className="text-xs text-muted-foreground">&ldquo;{request.reason}&rdquo;</p>
        ) : null}
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Badge variant="outline">{t(`status.${request.status}`)}</Badge>
          {pending ? <span>{t('expiresIn', { days })}</span> : null}
          {request.decision_note ? <span>{request.decision_note}</span> : null}
        </div>
      </div>
      {pending ? (
        <div className="flex shrink-0 gap-2">
          {box === 'inbox' ? (
            <>
              <Button
                size="sm"
                variant="outline"
                disabled={decide.isPending}
                onClick={() => run({ id: request.id, action: 'deny' }, 'denied')}
              >
                {t('deny')}
              </Button>
              <Button
                size="sm"
                disabled={decide.isPending}
                onClick={() => run({ id: request.id, action: 'approve' }, 'approved')}
              >
                {t('approve')}
              </Button>
            </>
          ) : (
            <Button
              size="sm"
              variant="outline"
              disabled={decide.isPending}
              onClick={() => run({ id: request.id, action: 'cancel' }, 'cancelled')}
            >
              {t('cancelRequest')}
            </Button>
          )}
        </div>
      ) : null}
    </li>
  )
}
