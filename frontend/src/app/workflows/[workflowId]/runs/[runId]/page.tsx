import { RunDetailClient } from './_components/run-detail-client'

export default async function WorkflowRunPage({
  params,
}: {
  params: Promise<{ workflowId: string; runId: string }>
}) {
  const { workflowId, runId } = await params
  return <RunDetailClient workflowId={workflowId} runId={runId} />
}
