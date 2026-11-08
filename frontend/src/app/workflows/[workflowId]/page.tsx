import { WorkflowEditorClient } from './_components/workflow-editor-client'

export default async function WorkflowEditorPage({
  params,
}: {
  params: Promise<{ workflowId: string }>
}) {
  const { workflowId } = await params
  return <WorkflowEditorClient workflowId={workflowId} />
}
