import { InvitationAccept } from './_components/invitation-accept'

export default async function InvitePage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params
  return <InvitationAccept token={decodeURIComponent(token)} />
}
