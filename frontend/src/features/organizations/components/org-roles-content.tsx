'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Skeleton } from '@/components/ui/skeleton'
import {
  useOrgCapabilities,
  useOrganizationInfo,
  useOrgRoles,
  usePrincipalGrantSet,
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
import { useOrgErrorMessage } from '../lib/error-message'
import { DirectoryPager } from './directory-pager'
import { OrgPageFrame } from './org-page-frame'
import { PrincipalPicker } from './principal-picker'

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

function principalKey(principal: Principal): string {
  return `${principal.type}:${principal.id}`
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

function toPrincipal(picked: PrincipalSearchView): Principal {
  if (picked.type === 'org') return { type: 'organization', id: picked.id }
  return { type: picked.type, id: picked.id }
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

interface SaveHandlers {
  onSuccess: () => void
  onError: (error: unknown) => void
}

type GrantKind = 'roles' | 'capabilities'

interface GrantSectionProps<Value extends string> {
  kind: GrantKind
  title: string
  description: string
  orgId: string
  /** Principals discovered on the current list page (one entry per grant). */
  principals: Principal[] | undefined
  pageState: {
    isLoading: boolean
    error: unknown
    data?: { has_more: boolean; next_cursor: string | null }
  }
  options: readonly Value[]
  optionLabel: (value: Value) => string
  allowedFor: (principal: Principal) => readonly Value[]
  pickerTypes: readonly ('user' | 'group' | 'org')[]
  canEdit: boolean
  isSaving: boolean
  names: Record<string, string>
  onPicked: (picked: PrincipalSearchView) => void
  onSave: (principal: Principal, values: Value[], version: string, handlers: SaveHandlers) => void
  pager: ReturnType<typeof useCursorPager>
}

function uniquePrincipals(principals: readonly Principal[]): Principal[] {
  const seen = new Map<string, Principal>()
  for (const principal of principals) seen.set(principalKey(principal), principal)
  return [...seen.values()]
}

function GrantSection<Value extends string>({
  kind,
  title,
  description,
  orgId,
  principals,
  pageState,
  options,
  optionLabel,
  allowedFor,
  pickerTypes,
  canEdit,
  isSaving,
  names,
  onPicked,
  onSave,
  pager,
}: GrantSectionProps<Value>) {
  const t = useTranslations('orgAdmin.roles')
  const errorMessage = useOrgErrorMessage()
  const [added, setAdded] = useState<Principal[]>([])
  const rows = uniquePrincipals([...(principals ?? []), ...added])

  return (
    <SettingsSectionCard title={title} description={description}>
      <div className="space-y-4">
        {canEdit ? (
          <PrincipalPicker
            orgId={orgId}
            types={pickerTypes}
            actionLabel={t('pick')}
            onPick={(picked) => {
              onPicked(picked)
              setAdded((current) => [...current, toPrincipal(picked)])
            }}
          />
        ) : null}
        {pageState.isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : !principals ? (
          <p className="text-sm text-status-danger">{errorMessage(pageState.error)}</p>
        ) : rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('empty')}</p>
        ) : (
          <ul className="divide-y divide-border">
            {rows.map((principal) => (
              <GrantRow
                key={principalKey(principal)}
                kind={kind}
                orgId={orgId}
                principal={principal}
                label={names[principalKey(principal)] ?? shortPrincipalId(principal.id)}
                typeLabel={t(`principal.${principal.type}`)}
                options={options}
                allowed={allowedFor(principal)}
                optionLabel={optionLabel}
                canEdit={canEdit}
                isSaving={isSaving}
                onSave={(values, version) =>
                  onSave(principal, values, version, {
                    onSuccess: () => toast.success(t('saved')),
                    onError: (err) => toast.error(errorMessage(err)),
                  })
                }
              />
            ))}
          </ul>
        )}
        <DirectoryPager
          canGoBack={pager.canGoBack}
          hasMore={pageState.data?.has_more ?? false}
          onBack={pager.back}
          onNext={() => pager.next(pageState.data?.next_cursor ?? null)}
        />
      </div>
    </SettingsSectionCard>
  )
}

/** The grant API returns ids only; show a short, stable id until names are known. */
function shortPrincipalId(id: string): string {
  return id.length > 8 ? id.slice(0, 8) : id
}

function sameSet(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((value) => b.includes(value))
}

function GrantRow<Value extends string>({
  kind,
  orgId,
  principal,
  label,
  typeLabel,
  options,
  allowed,
  optionLabel,
  canEdit,
  isSaving,
  onSave,
}: {
  kind: GrantKind
  orgId: string
  principal: Principal
  label: string
  typeLabel: string
  options: readonly Value[]
  allowed: readonly Value[]
  optionLabel: (value: Value) => string
  canEdit: boolean
  isSaving: boolean
  onSave: (values: Value[], version: string) => void
}) {
  const t = useTranslations('orgAdmin.roles')
  const tCommon = useTranslations('orgAdmin.common')
  // The full set (all pages, server-filtered) — a replace write overwrites
  // everything, so Save stays disabled until it has loaded.
  const grantSet = usePrincipalGrantSet(orgId, kind, principal)
  const serverValues = (grantSet.data?.items ?? []).map((grant) =>
    'role' in grant ? grant.role : grant.capability,
  ) as Value[]
  const loaded = grantSet.data !== undefined
  const [seed, setSeed] = useState<Value[] | null>(null)
  const [selected, setSelected] = useState<Value[]>([])

  // Re-seed this row only when *its* server values change, and never over an
  // unsaved edit (unless the edit already matches what the server now has).
  if (loaded && (seed === null || !sameSet(seed, serverValues))) {
    const untouched = seed === null || sameSet(selected, seed)
    if (untouched || sameSet(selected, serverValues)) {
      setSeed(serverValues)
      setSelected(serverValues)
    }
  }

  const dirty = seed !== null && !sameSet(selected, seed)
  const legend = t('rowLegend', { name: label, type: typeLabel })

  return (
    <li className="py-3">
      <fieldset className="space-y-2" aria-busy={!loaded}>
        <legend className="flex items-center gap-2">
          <span className="truncate text-sm font-medium">{label}</span>
          <Badge variant="outline">{typeLabel}</Badge>
          <span className="sr-only">{legend}</span>
        </legend>
        {grantSet.isError ? <p className="text-xs text-status-danger">{t('loadFailed')}</p> : null}
        <div className="flex flex-wrap gap-3">
          {options.map((value) => {
            const id = `${kind}-${principal.type}-${principal.id}-${value}`
            return (
              <label key={value} htmlFor={id} className="flex items-center gap-1.5 text-sm">
                <Checkbox
                  id={id}
                  checked={selected.includes(value)}
                  disabled={!canEdit || !loaded || !allowed.includes(value)}
                  onCheckedChange={(checked) =>
                    setSelected((current) =>
                      checked ? [...current, value] : current.filter((item) => item !== value),
                    )
                  }
                />
                {optionLabel(value)}
              </label>
            )
          })}
        </div>
        {canEdit ? (
          <Button
            size="sm"
            aria-label={t('saveFor', { name: label })}
            disabled={!loaded || !dirty || isSaving}
            onClick={() => {
              if (grantSet.data) onSave(selected, grantSet.data.updated_at)
            }}
          >
            {tCommon('save')}
          </Button>
        ) : null}
      </fieldset>
    </li>
  )
}
