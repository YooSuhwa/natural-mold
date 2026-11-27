import { z } from 'zod'

export const independentJobSchema = z.object({
  id: z.uuid(),
  parent_agent_id: z.uuid(),
  parent_conversation_id: z.uuid(),
  parent_run_id: z.uuid(),
  child_agent_id: z.uuid(),
  child_agent_name: z.string(),
  child_conversation_id: z.uuid(),
  child_run_id: z.uuid(),
  reason_code: z.string().nullish(),
  status: z.enum([
    'queued',
    'running',
    'waiting_for_approval',
    'canceling',
    'canceled',
    'completed',
    'failed',
    'needs_review',
  ]),
})

export const independentJobListSchema = z.object({
  enabled: z.boolean(),
  parent_job: independentJobSchema.nullable(),
  jobs: z.array(independentJobSchema),
  targets: z.array(z.object({ id: z.uuid(), name: z.string() })),
})

export type IndependentChatJob = z.infer<typeof independentJobSchema>
export type IndependentJobList = z.infer<typeof independentJobListSchema>

export function isOpenIndependentJob(job: IndependentChatJob): boolean {
  return ['queued', 'running', 'waiting_for_approval', 'canceling'].includes(job.status)
}
