'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'

import { Skeleton } from '@/components/ui/skeleton'
import {
  useOrgCapabilities,
  useOrganizationInfo,
  useOrgRoles,
  useReplaceOrgCapabilities,
  useReplaceOrgRoles,
} from '@/lib/hooks/use-organizations'
import type {
  OrgCapability,
  OrgRole,
  OrganizationSummary,
  Principal,
  PrincipalSearchView,
} from '@/lib/types/organization'
import { orgAccess } from '../lib/access'
import { useCursorPager } from '../lib/cursor-pager'
import { GrantSection, principalKey, toPrincipal } from './org-grant-section'
import { OrgPageFrame } from './org-page-frame'
import { RoleDefaultsMatrix } from './org-role-defaults'

export const ORG_ROLES: readonly OrgRole[] = ['admin', 'builder', 'auditor']
export const ORG_CAPABILITIES: readonly OrgCapability[] = [
  'cap_create_agent',
  'cap_create_skill',
  'cap_create_tool',
  'cap_register_mcp',
  'cap_connect_database',
  'cap_share_org_wide',
  'cap_publish_marketplace',
]
/** Backend `CapabilityAssignment.valid_targets`: these need a user or group principal. */
const NON_ORG_CAPABILITIES: readonly OrgCapability[] = [
  'cap_connect_database',
  'cap_share_org_wide',
]

const ROLE_PICKER_TYPES = ['user', 'group'] as const
const CAPABILITY_PICKER_TYPES = ['user', 'group', 'org'] as const

interface PrincipalGrants<Value extends string> {
  principal: Principal
  values: Value[]
}

/** Group a grant page by principal (one row per grant on the wire). */
export function groupGrantsByPrincipal<Value extends string>(
  items: readonly { principal: Principal; value: Value }[],
): PrincipalGrants<Value>[] {
  const byKey = new Map<string, PrincipalGrants<Value>>()
  for (const item of items) {
    const key = principalKey(item.principal)
    const entry = byKey.get(key) ?? { principal: item.principal, values: [] }
    entry.values.push(item.value)
    byKey.set(key, entry)
  }
  return [...byKey.values()]
}

export function OrgRolesContent() {
  const t = useTranslations('orgAdmin.roles')
  return (
    <OrgPageFrame title={t('title')} description={t('description')}>
      {(organization) => <OrgRolesBody organization={organization} />}
    </OrgPageFrame>
  )
}

function OrgRolesBody({ organization }: { organization: OrganizationSummary }) {
  const t = useTranslations('orgAdmin.roles')
  const info = useOrganizationInfo(organization.id)
  const access = orgAccess(organization, info.data?.permissions)
  const [names, setNames] = useState<Record<string, string>>({})
  const rememberName = (picked: PrincipalSearchView) =>
    setNames((current) => ({
      ...current,
      [principalKey(toPrincipal(picked))]: picked.display_name,
    }))

  if (info.isLoading) return <Skeleton className="h-32 w-full" />
  if (!access.canReadGrants) {
    return <p className="text-sm text-muted-foreground">{t('noAccess')}</p>
  }
  return (
    <div className="space-y-4">
      <RoleDefaultsMatrix />
      <RoleGrantsSection
        orgId={organization.id}
        canEdit={access.canManageMembers}
        names={names}
        onPicked={rememberName}
      />
      <CapabilityGrantsSection
        orgId={organization.id}
        canEdit={access.canManageMembers}
        names={names}
        onPicked={rememberName}
      />
    </div>
  )
}

interface SectionProps {
  orgId: string
  canEdit: boolean
  names: Record<string, string>
  onPicked: (picked: PrincipalSearchView) => void
}

function RoleGrantsSection({ orgId, canEdit, names, onPicked }: SectionProps) {
  const t = useTranslations('orgAdmin.roles')
  const pager = useCursorPager()
  const page = useOrgRoles(orgId, { cursor: pager.cursor })
  const replace = useReplaceOrgRoles(orgId)
  return (
    <GrantSection
      kind="roles"
      title={t('roleSection.title')}
      description={t('roleSection.description')}
      orgId={orgId}
      principals={page.data?.items.map((grant) => grant.principal)}
      pageState={page}
      options={ORG_ROLES}
      optionLabel={(role) => t(`role.${role}`)}
      allowedFor={() => ORG_ROLES}
      pickerTypes={ROLE_PICKER_TYPES}
      canEdit={canEdit}
      isSaving={replace.isPending}
      names={names}
      onPicked={onPicked}
      onSave={(principal, values, version, handlers) => {
        if (principal.type === 'organization') return
        replace.mutate({ data: { principal, roles: values }, version }, handlers)
      }}
      pager={pager}
    />
  )
}

function CapabilityGrantsSection({ orgId, canEdit, names, onPicked }: SectionProps) {
  const t = useTranslations('orgAdmin.roles')
  const pager = useCursorPager()
  const page = useOrgCapabilities(orgId, { cursor: pager.cursor })
  const replace = useReplaceOrgCapabilities(orgId)
  return (
    <GrantSection
      kind="capabilities"
      title={t('capabilitySection.title')}
      description={t('capabilitySection.description')}
      orgId={orgId}
      principals={page.data?.items.map((grant) => grant.principal)}
      pageState={page}
      options={ORG_CAPABILITIES}
      optionLabel={(capability) => t(`capability.${capability}`)}
      allowedFor={(principal) =>
        principal.type === 'organization'
          ? ORG_CAPABILITIES.filter((c) => !NON_ORG_CAPABILITIES.includes(c))
          : ORG_CAPABILITIES
      }
      pickerTypes={CAPABILITY_PICKER_TYPES}
      canEdit={canEdit}
      isSaving={replace.isPending}
      names={names}
      onPicked={onPicked}
      onSave={(principal, values, version, handlers) =>
        replace.mutate({ data: { principal, capabilities: values }, version }, handlers)
      }
      pager={pager}
    />
  )
}
