import { redirect } from 'next/navigation'

export default function MarketplaceModerationRedirectPage() {
  redirect('/settings/marketplace-admin')
}
