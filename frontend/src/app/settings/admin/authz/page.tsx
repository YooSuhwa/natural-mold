import { AuthzSyncPageClient } from './authz-sync-page-client'

/** S27 권한 동기화 상태 (plan 14.3; H-04): server wrapper for the operator client page. */
export default function AuthzSyncSettingsPage() {
  return <AuthzSyncPageClient />
}
