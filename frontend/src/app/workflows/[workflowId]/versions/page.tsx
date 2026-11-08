import { VersionsPageClient } from './_components/versions-page-client'

export default async function WorkflowVersionsPage({
  params,
}: {
  params: Promise<{ workflowId: string }>
}) {
  const { workflowId } = await params
  return <VersionsPageClient workflowId={workflowId} />
}
