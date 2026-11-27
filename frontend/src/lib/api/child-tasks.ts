import { z } from 'zod'
import { apiFetch } from './client'

const instruction = z.object({
  id: z.string(),
  client_request_id: z.string(),
  text: z.string(),
  status: z.enum(['pending', 'delivering', 'applied', 'review_required', 'cancelled']),
  created_at: z.string(),
  applied_at: z.string().nullable(),
})

const childTask = z.object({
  id: z.string(),
  run_id: z.string(),
  tool_call_id: z.string(),
  role: z.string(),
  brief: z.string().nullish(),
  child_agent_id: z.uuid().nullish(),
  status: z.enum(['running', 'awaiting_approval', 'completed', 'cancelled', 'failed']),
  checkpointed: z.boolean(),
  created_at: z.string(),
  updated_at: z.string(),
  result: z.string().nullable(),
  instructions: z.array(instruction),
})

const childTaskPage = z.object({
  items: z.array(childTask),
  next_offset: z.number().int().min(0).max(2147483647).nullable(),
  enabled: z.boolean(),
  allow_legacy_details: z.boolean(),
})

export type ChildTaskPage = z.infer<typeof childTaskPage>
export type ChildTask = z.infer<typeof childTask>
export type ChildInstructionRequest = Pick<
  z.infer<typeof instruction>,
  'client_request_id' | 'text'
>

export function isActiveChildTask(task: ChildTask): boolean {
  return task.status === 'running' || task.status === 'awaiting_approval'
}

function tasksPath(conversationId: string) {
  return `/api/conversations/${encodeURIComponent(conversationId)}/child-tasks`
}

function taskPath(conversationId: string, taskId: string) {
  return `${tasksPath(conversationId)}/${encodeURIComponent(taskId)}`
}

export async function listChildTasks(conversationId: string, signal?: AbortSignal, offset = 0) {
  const page = childTaskPage.parse(
    await apiFetch<unknown>(`${tasksPath(conversationId)}?limit=50&offset=${offset}`, { signal }),
  )
  if (page.next_offset !== null && page.next_offset <= offset) {
    throw new Error('Child task page offset did not advance')
  }
  return page
}

export async function sendChildInstruction(
  conversationId: string,
  taskId: string,
  request: ChildInstructionRequest,
) {
  return childTask.parse(
    await apiFetch<unknown>(`${taskPath(conversationId, taskId)}/instructions`, {
      method: 'POST',
      body: JSON.stringify(request),
    }),
  )
}

export async function cancelChildTask(conversationId: string, taskId: string) {
  return childTask.parse(
    await apiFetch<unknown>(`${taskPath(conversationId, taskId)}/cancel`, { method: 'POST' }),
  )
}

export function getChildTask(conversationId: string, taskId: string, signal?: AbortSignal) {
  return readChildTask(conversationId, taskId, 0, signal)
}

export function waitForChildTask(conversationId: string, taskId: string, signal?: AbortSignal) {
  return readChildTask(conversationId, taskId, 10, signal)
}

async function readChildTask(
  conversationId: string,
  taskId: string,
  timeout: 0 | 10,
  signal?: AbortSignal,
) {
  return childTask.parse(
    await apiFetch<unknown>(`${taskPath(conversationId, taskId)}/wait?timeout_seconds=${timeout}`, {
      signal,
    }),
  )
}
