'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { FormFieldShell } from '@/components/shared/form-field-shell'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { useUpdateOrganization } from '@/lib/hooks/use-organizations'
import type {
  DefaultMemberRole,
  OrganizationSettings,
  OrganizationView,
  RegistrationPolicy,
} from '@/lib/types/organization'
import { useOrgErrorMessage } from '../lib/error-message'
import { OrgSelectField } from './org-select-field'

const MEMBER_ROLES: readonly DefaultMemberRole[] = ['member', 'builder']
/** `inherit` = `registration_policy: null` (the company policy applies). */
type RegistrationChoice = RegistrationPolicy | 'inherit'
const REGISTRATION: readonly RegistrationChoice[] = [
  'inherit',
  'invite_only',
  'default_org',
  'closed',
]

/** S08 "가입·공유 정책": default role, join method and public links (`PATCH /api/orgs/{id}`). */
export function OrgPolicySection({ view, canEdit }: { view: OrganizationView; canEdit: boolean }) {
  // Remount on a new version so a refetch (also after stale_version) reseeds the form.
  return <OrgPolicyForm key={view.updated_at} view={view} canEdit={canEdit} />
}

function OrgPolicyForm({ view, canEdit }: { view: OrganizationView; canEdit: boolean }) {
  const t = useTranslations('orgAdmin.settings.policy')
  const errorMessage = useOrgErrorMessage()
  const update = useUpdateOrganization(view.id)
  const initial = view.settings
  const [role, setRole] = useState<DefaultMemberRole>(initial.default_member_role)
  const [registration, setRegistration] = useState<RegistrationChoice>(
    initial.registration_policy ?? 'inherit',
  )
  const [publicLinks, setPublicLinks] = useState(initial.allow_public_share_links)
  const next: OrganizationSettings = {
    default_member_role: role,
    registration_policy: registration === 'inherit' ? null : registration,
    allow_public_share_links: publicLinks,
  }
  const dirty =
    next.default_member_role !== initial.default_member_role ||
    next.registration_policy !== initial.registration_policy ||
    next.allow_public_share_links !== initial.allow_public_share_links

  function save() {
    update.mutate(
      { data: { settings: next }, version: view.updated_at },
      {
        onSuccess: () => toast.success(t('saved')),
        onError: (error) => toast.error(errorMessage(error)),
      },
    )
  }

  return (
    <SettingsSectionCard title={t('title')} description={t('description')}>
      <div className="space-y-4">
        <OrgSelectField
          id="org-default-role"
          label={t('defaultRole')}
          description={t('defaultRoleHint')}
          value={role}
          options={MEMBER_ROLES}
          optionLabel={(value) => t(`role.${value}`)}
          disabled={!canEdit}
          onChange={setRole}
        />
        <OrgSelectField
          id="org-registration"
          label={t('registration')}
          description={t('registrationHint')}
          value={registration}
          options={REGISTRATION}
          optionLabel={(value) => t(`registrationOption.${value}`)}
          disabled={!canEdit}
          onChange={setRegistration}
        />
        <FormFieldShell
          id="org-public-links"
          label={t('publicLinks')}
          description={t('publicLinksHint')}
          layout="inline"
        >
          <Switch
            id="org-public-links"
            checked={publicLinks}
            disabled={!canEdit}
            onCheckedChange={setPublicLinks}
          />
        </FormFieldShell>
        {canEdit ? (
          <Button onClick={save} disabled={!dirty || update.isPending}>
            {t('submit')}
          </Button>
        ) : (
          <p className="text-xs text-muted-foreground">{t('readOnly')}</p>
        )}
      </div>
    </SettingsSectionCard>
  )
}
