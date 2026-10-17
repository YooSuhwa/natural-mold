'use client'

import { useTranslations } from 'next-intl'
import { CheckIcon, MinusIcon } from 'lucide-react'

import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import type { OrgCapability } from '@/lib/types/organization'

type MatrixRole = 'admin' | 'builder' | 'member' | 'auditor'
type MatrixRow = OrgCapability | 'view_activity'

const ROLES: readonly MatrixRole[] = ['admin', 'builder', 'member', 'auditor']

/**
 * Fixed role defaults (S14 ✓ columns). Mirrors backend `app/authz/org_permissions.py`
 * (`BUILDER_CAPABILITIES`, `ADMIN_CAPABILITIES`) and `can_view_usage`/`can_view_audit`
 * for admins and auditors (plan 9.6). Display only; the server decides.
 */
const BUILDER: readonly MatrixRow[] = [
  'cap_create_agent',
  'cap_create_skill',
  'cap_create_tool',
  'cap_register_mcp',
  'cap_publish_marketplace',
]
export const ROLE_DEFAULTS: Record<MatrixRole, readonly MatrixRow[]> = {
  admin: [...BUILDER, 'cap_connect_database', 'cap_share_org_wide', 'view_activity'],
  builder: BUILDER,
  member: [],
  auditor: ['view_activity'],
}
const ROWS: readonly MatrixRow[] = [
  'cap_create_agent',
  'cap_create_skill',
  'cap_create_tool',
  'cap_register_mcp',
  'cap_connect_database',
  'cap_share_org_wide',
  'cap_publish_marketplace',
  'view_activity',
]

/** S14: what each role has by default (fixed); extra grants are edited below. */
export function RoleDefaultsMatrix() {
  const t = useTranslations('orgAdmin.roles')
  return (
    <SettingsSectionCard title={t('defaults.title')} description={t('defaults.description')}>
      <div className="space-y-3">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t('defaults.feature')}</TableHead>
              {ROLES.map((role) => (
                <TableHead key={role} className="text-center">
                  {t(`defaults.role.${role}`)}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {ROWS.map((row) => (
              <TableRow key={row}>
                <TableCell>
                  {row === 'view_activity' ? t('defaults.viewActivity') : t(`capability.${row}`)}
                </TableCell>
                {ROLES.map((role) => {
                  const granted = ROLE_DEFAULTS[role].includes(row)
                  const Icon = granted ? CheckIcon : MinusIcon
                  return (
                    <TableCell key={role} className="text-center">
                      <Icon
                        className={
                          granted ? 'mx-auto size-4' : 'mx-auto size-4 text-muted-foreground'
                        }
                        aria-hidden="true"
                      />
                      <span className="sr-only">
                        {granted ? t('defaults.granted') : t('defaults.notGranted')}
                      </span>
                    </TableCell>
                  )
                })}
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <p className="text-xs text-muted-foreground">{t('defaults.privacyNote')}</p>
      </div>
    </SettingsSectionCard>
  )
}
