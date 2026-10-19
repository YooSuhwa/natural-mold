import {
  Building2Icon,
  InboxIcon,
  LayersIcon,
  ShieldIcon,
  UsersIcon,
  UsersRoundIcon,
} from 'lucide-react'

import type { SidebarNavItemConfig } from '@/components/layout/sidebar-nav-section'
import type { OrgAccess } from '@/features/organizations/lib/access'

type Translate = (key: string) => string

/**
 * Organization settings entries (S08–S14, S18). Role grants need member management
 * (`org_members_context`), the catalog policy needs `can_manage_policy`; fail closed.
 */
export function organizationNavItems(access: OrgAccess, t: Translate): SidebarNavItemConfig[] {
  return [
    {
      href: '/settings/organization',
      label: t('nav.organizationSettings'),
      icon: Building2Icon,
      exact: true,
    },
    {
      href: '/settings/organization/members',
      label: t('nav.organizationMembers'),
      icon: UsersIcon,
    },
    {
      href: '/settings/organization/groups',
      label: t('nav.organizationGroups'),
      icon: UsersRoundIcon,
    },
    // Anyone may send requests; the inbox shows what the caller can share (S18).
    {
      href: '/settings/organization/requests',
      label: t('nav.organizationRequests'),
      icon: InboxIcon,
    },
    ...(access.canReadGrants
      ? [
          {
            href: '/settings/organization/roles',
            label: t('nav.organizationRoles'),
            icon: ShieldIcon,
          },
        ]
      : []),
    ...(access.canManagePolicy
      ? [
          {
            href: '/settings/organization/catalog',
            label: t('nav.organizationCatalog'),
            icon: LayersIcon,
          },
        ]
      : []),
  ]
}
