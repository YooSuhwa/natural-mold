'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { ApiErrorState } from '@/components/shared/api-error-state'
import { DeleteConfirmDialog } from '@/components/shared/delete-confirm-dialog'
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
import { DirectoryPager } from '@/features/organizations/components/directory-pager'
import { useCursorPager } from '@/features/organizations/lib/cursor-pager'
import { useOrgErrorMessage } from '@/features/organizations/lib/error-message'
import { useTenantMemberMutations, useTenantMembers } from '@/lib/hooks/use-tenant-admin'
import type { TenantMember, TenantRole } from '@/lib/types/tenant-admin'
import { CompanyAdminGate, type CompanyScope } from './company-admin-gate'
import { CompanyInvitations } from './company-invitations'

const ROLES: readonly TenantRole[] = ['admin', 'auditor']

/** S06 company members, company admin/auditor roles and company invitations (admins only). */
export function CompanyMembersContent() {
  const t = useTranslations('orgAdmin.companySettings.members')
  return (
    <CompanyAdminGate title={t('title')} description={t('description')} requireAdmin>
      {(scope) => (
        <div className="space-y-4">
          <MembersSection scope={scope} />
          <CompanyInvitations scope={scope} />
        </div>
      )}
    </CompanyAdminGate>
  )
}

function MembersSection({ scope }: { scope: CompanyScope }) {
  const t = useTranslations('orgAdmin.companySettings.members')
  const pager = useCursorPager()
  const page = useTenantMembers(scope.tenantId, pager.cursor)
  const [removing, setRemoving] = useState<TenantMember | null>(null)
  const mutations = useTenantMemberMutations(scope.tenantId)
  const errorMessage = useOrgErrorMessage()
  const version = scope.overview.updated_at
  const feedback = {
    onSuccess: () => toast.success(t('updated')),
    onError: (err: unknown) => toast.error(errorMessage(err)),
  }

  function toggleRole(member: TenantMember, role: TenantRole) {
    const roles = member.roles.includes(role)
      ? member.roles.filter((value) => value !== role)
      : [...member.roles, role]
    mutations.replaceRoles.mutate({ userId: member.user_id, roles, version }, feedback)
  }

  return (
    <SettingsSectionCard title={t('listTitle')} description={t('listDescription')}>
      {page.isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : !page.data ? (
        <ApiErrorState error={page.error} onRetry={() => void page.refetch()} />
      ) : page.data.items.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t('empty')}</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t('name')}</TableHead>
              <TableHead>{t('roles')}</TableHead>
              <TableHead>{t('status')}</TableHead>
              <TableHead>{t('actions')}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {page.data.items.map((member) => (
              <TableRow key={member.user_id}>
                <TableCell>
                  <span className="block text-sm">{member.display_name}</span>
                  <span className="moldy-ui-caption text-muted-foreground">{member.email}</span>
                </TableCell>
                <TableCell>
                  <div className="flex flex-wrap gap-1">
                    {member.roles.length === 0 ? (
                      <span className="text-sm text-muted-foreground">{t('role.member')}</span>
                    ) : (
                      member.roles.map((role) => (
                        <Badge key={role} variant="secondary">
                          {t(`role.${role}`)}
                        </Badge>
                      ))
                    )}
                  </div>
                </TableCell>
                <TableCell>
                  <Badge variant={member.status === 'active' ? 'secondary' : 'outline'}>
                    {t(`memberStatus.${member.status}`)}
                  </Badge>
                </TableCell>
                <TableCell>
                  {member.status === 'removed' ? null : (
                    <div className="flex flex-wrap gap-1">
                      {ROLES.map((role) => {
                        const has = member.roles.includes(role)
                        return (
                          <Button
                            key={role}
                            size="sm"
                            variant="outline"
                            disabled={mutations.replaceRoles.isPending}
                            aria-label={t(has ? `revoke.${role}` : `grant.${role}`, {
                              name: member.display_name,
                            })}
                            onClick={() => toggleRole(member, role)}
                          >
                            {t(has ? `revokeShort.${role}` : `grantShort.${role}`)}
                          </Button>
                        )
                      })}
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={mutations.setStatus.isPending}
                        aria-label={t(member.status === 'active' ? 'suspend' : 'reactivate', {
                          name: member.display_name,
                        })}
                        onClick={() =>
                          mutations.setStatus.mutate(
                            {
                              userId: member.user_id,
                              status: member.status === 'active' ? 'suspended' : 'active',
                              version,
                            },
                            feedback,
                          )
                        }
                      >
                        {t(member.status === 'active' ? 'suspendShort' : 'reactivateShort')}
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        aria-label={t('remove', { name: member.display_name })}
                        onClick={() => setRemoving(member)}
                      >
                        {t('removeShort')}
                      </Button>
                    </div>
                  )}
                </TableCell>
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
      <DeleteConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => (open ? undefined : setRemoving(null))}
        title={t('removeTitle')}
        description={t('removeDescription', { name: removing?.display_name ?? '' })}
        confirmLabel={t('removeShort')}
        isPending={mutations.remove.isPending}
        onConfirm={() => {
          if (!removing) return
          mutations.remove.mutate(
            { userId: removing.user_id, version },
            { ...feedback, onSettled: () => setRemoving(null) },
          )
        }}
      />
    </SettingsSectionCard>
  )
}
