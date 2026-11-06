import { ApprovalsPageClient, type ApprovalTab } from './_components/approvals-page-client'

const TABS: readonly ApprovalTab[] = ['pending', 'done', 'runs', 'stops']

export default async function ApprovalsPage({
  searchParams,
}: {
  searchParams: Promise<{ tab?: string }>
}) {
  const { tab } = await searchParams
  const initialTab = TABS.find((value) => value === tab) ?? 'pending'
  return <ApprovalsPageClient initialTab={initialTab} />
}
