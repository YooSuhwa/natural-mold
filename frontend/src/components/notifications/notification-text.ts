import type { NotificationView } from '@/lib/types/notification'

type Translate = (key: string, values?: Record<string, string>) => string

const LEVELS = new Set(['consumer', 'viewer', 'editor', 'manager'])
const REASONS = new Set([
  'permission_revoked',
  'owner_left',
  'auth_required',
  'delegation_broken',
  'required_unavailable',
  'delegator_lost_access',
  'resource_unavailable',
])

function field(item: NotificationView, key: string): string | null {
  const value = item.payload[key]
  return typeof value === 'string' ? value : null
}

/** The S25 line of one notification (14.2 kinds); unknown kinds get a generic line. */
export function notificationText(item: NotificationView, t: Translate): string {
  const target = item.target?.accessible ? (item.target.name ?? t('unnamed')) : t('hidden')
  const actor = item.actor?.display_name ?? t('someone')
  const level = field(item, 'level')
  const reason = field(item, 'reason')
  const values = {
    target,
    actor,
    level: level && LEVELS.has(level) ? t(`levels.${level}`) : t('levels.other'),
    reason: reason && REASONS.has(reason) ? t(`reasons.${reason}`) : t('reasons.other'),
    trigger: field(item, 'trigger_name') ?? t('unnamed'),
  }
  switch (item.kind) {
    case 'access_request.created':
      return t('kinds.accessRequestCreated', values)
    case 'access_request.decided':
      return field(item, 'decision') === 'approved'
        ? t('kinds.accessRequestApproved', values)
        : t('kinds.accessRequestDenied', values)
    case 'grant.received':
      return t('kinds.grantReceived', values)
    case 'trigger.blocked':
      return t('kinds.triggerBlocked', values)
    case 'connection.owner_needed':
      return t('kinds.ownerNeeded', values)
    case 'delegation.broken':
      return t('kinds.delegationBroken', values)
    case 'ownership.transferred':
      return field(item, 'role') === 'previous_owner'
        ? t('kinds.ownershipPrevious', values)
        : t('kinds.ownershipNew', values)
    default:
      return t('kinds.generic', values)
  }
}
