import { RunsPageClient } from './_components/runs-page-client'

export default async function WorkflowRunsPage({
  params,
}: {
  params: Promise<{ workflowId: string }>
}) {
  const { workflowId } = await params
  return <RunsPageClient workflowId={workflowId} />
}
