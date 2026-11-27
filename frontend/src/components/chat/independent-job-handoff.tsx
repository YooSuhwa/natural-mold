'use client'

import { useIndependentJobListing } from '@/lib/hooks/use-independent-chat-jobs'
import { IndependentJobForm } from './independent-job-form'

export function IndependentJobHandoff({
  conversationId,
  runId,
  childTaskId,
  childAgentId,
}: {
  conversationId: string
  runId: string
  childTaskId: string
  childAgentId: string | null | undefined
}) {
  const listing = useIndependentJobListing(childAgentId ? conversationId : null, runId)
  const data = listing.query.data
  if (!listing.query.isSuccess || !data?.enabled || data.parent_job) return null
  const target = data.targets.find((candidate) => candidate.id === childAgentId)
  if (!target) return null
  return (
    <IndependentJobForm
      key={childTaskId}
      conversationId={conversationId}
      runId={runId}
      targets={[target]}
      childTaskId={childTaskId}
      onJob={listing.applyJob}
    />
  )
}
