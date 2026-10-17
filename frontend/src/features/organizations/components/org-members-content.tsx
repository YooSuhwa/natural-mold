'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  useAddOrgMember,
  useOrgMembers,
  useSetOrgMemberStatus,
  useOrganizationInfo,
} from '@/lib/hooks/use-organizations'
import type { MemberView, OrganizationSummary } from '@/lib/types/organization'
import { formatDisplayDate } from '@/lib/utils/display-format'
import { orgAccess } from '../lib/access'
import { useCursorPager } from '../lib/cursor-pager'
import { useOrgErrorMessage } from '../lib/error-message'
import { DirectoryPager } from './directory-pager'
import { MemberRemovalWizard } from './member-removal/member-removal-wizard'
import { OrgPageFrame } from './org-page-frame'
import { PrincipalPicker } from './principal-picker'

const USER_ONLY = ['user'] as const

export function OrgMembersContent() {
  const t = useTranslations('orgAdmin.members')
  return (
    <OrgPageFrame title={t('title')} description={t('description')}>
      {(organization) => <OrgMembersBody organization={organization} />}
    </OrgPageFrame>
  )
}

function OrgMembersBody({ organization }: { organization: OrganizationSummary }) {
  const t = useTranslations('orgAdmin.members')
  const errorMessage = useOrgErrorMessage()
  const pager = useCursorPager()
  const page = useOrgMembers(organization.id, { cursor: pager.cursor })
  const info = useOrganizationInfo(organization.id)
  // Server permissions also cover company administrators who are plain org members.
  const access = orgAccess(organization, info.data?.permissions)
  const add = useAddOrgMember(organization.id)
  const setStatus = useSetOrgMemberStatus(organization.id)
  const [removing, setRemoving] = useState<MemberView | null>(null)

  const version = page.data?.updated_at
  const onError = (err: unknown) => toast.error(errorMessage(err))
  const busy = add.isPending || setStatus.isPending

  return (
    <div className="space-y-4">
      {access.canManageMembers && version ? (
        <SettingsSectionCard title={t('add.title')} description={t('add.description')}>
          <PrincipalPicker
            orgId={organization.id}
            types={USER_ONLY}
            actionLabel={t('add.submit')}
            disabled={busy}
            onPick={(principal) =>
              add.mutate(
                { userId: principal.id, version },
                { onSuccess: () => toast.success(t('add.done')), onError },
              )
            }
          />
        </SettingsSectionCard>
      ) : null}

      <SettingsSectionCard title={t('list.title')}>
        {page.isLoading ? (
          <Skeleton className="h-32 w-full" />
        ) : !page.data ? (
          <p className="text-sm text-status-danger">{errorMessage(page.error)}</p>
        ) : page.data.items.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('list.empty')}</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t('list.name')}</TableHead>
                <TableHead>{t('list.status')}</TableHead>
                <TableHead>{t('list.joined')}</TableHead>
                {access.canManageMembers ? <TableHead>{t('list.actions')}</TableHead> : null}
              </TableRow>
            </TableHeader>
            <TableBody>
              {page.data.items.map((member) => (
                <TableRow key={member.user_id}>
                  <TableCell>
                    <span className="block text-sm">{member.display_name}</span>
                    {member.email ? (
                      <span className="block text-xs text-muted-foreground">{member.email}</span>
                    ) : null}
                  </TableCell>
                  <TableCell>
                    <Badge variant={member.status === 'active' ? 'secondary' : 'outline'}>
                      {t(`status.${member.status}`)}
                    </Badge>
                  </TableCell>
                  <TableCell>{formatDisplayDate(member.joined_at)}</TableCell>
                  {access.canManageMembers && version ? (
                    <TableCell className="space-x-2">
                      {member.status !== 'removed' ? (
                        <>
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={busy}
                            onClick={() =>
                              setStatus.mutate(
                                {
                                  userId: member.user_id,
                                  status: member.status === 'active' ? 'suspended' : 'active',
                                  version,
                                },
                                { onError },
                              )
                            }
                          >
                            {member.status === 'active'
                              ? t('actions.suspend')
                              : t('actions.reactivate')}
                          </Button>
                          <Button
                            size="sm"
                            variant="destructive"
                            disabled={busy}
                            onClick={() => setRemoving(member)}
                          >
                            {t('actions.remove')}
                          </Button>
                        </>
                      ) : null}
                    </TableCell>
                  ) : access.canManageMembers ? (
                    <TableCell />
                  ) : null}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        <DirectoryPager
          canGoBack={pager.canGoBack}
          hasMore={page.data?.has_more ?? false}
          onBack={pager.back}
          onNext={() => pager.next(page.data?.next_cursor ?? null)}
        />
      </SettingsSectionCard>

      {version ? (
        <MemberRemovalWizard
          orgId={organization.id}
          orgName={organization.name}
          member={
            removing ? { userId: removing.user_id, displayName: removing.display_name } : null
          }
          version={version}
          onClose={() => setRemoving(null)}
        />
      ) : null}
    </div>
  )
}
