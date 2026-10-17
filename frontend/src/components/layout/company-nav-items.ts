import { BuildingIcon, ScaleIcon, ServerCogIcon, ShieldIcon, UserCogIcon } from 'lucide-react'

import type { SidebarNavItemConfig } from '@/components/layout/sidebar-nav-section'
import type { TenantAccess } from '@/lib/types/tenant-admin'

type Translate = (key: string) => string

/**
 * Company settings entries (S05–S07, S30). Fail closed: nothing shows until the caller's
 * company authority is known. Plain members only read the company policies.
 */
export function companyNavItems(
  access: TenantAccess | undefined,
  t: Translate,
): SidebarNavItemConfig[] {
  if (!access) return []
  const overview: SidebarNavItemConfig = {
    href: '/settings/company',
    label: t('nav.companyOrganizations'),
    icon: BuildingIcon,
    exact: true,
  }
  const policies: SidebarNavItemConfig = {
    href: '/settings/company/policies',
    label: t('nav.companyPolicies'),
    icon: ScaleIcon,
  }
  if (access === 'member') return [policies]
  if (access === 'auditor') return [overview, policies]
  return [
    overview,
    { href: '/settings/company/members', label: t('nav.companyMembers'), icon: UserCogIcon },
    policies,
    { href: '/settings/company/models', label: t('nav.companyModels'), icon: ServerCogIcon },
    { href: '/settings/company/sso', label: t('nav.companySso'), icon: ShieldIcon },
  ]
}
