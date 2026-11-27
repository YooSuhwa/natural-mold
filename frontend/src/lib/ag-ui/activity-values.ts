import { z } from 'zod'
import type { RecalledMemoryBrief } from '@/lib/stores/chat-memory-recall'
import type { SkillDraftBrief, SkillValidationSnapshot } from '@/lib/stores/chat-skill-builder'
import type {
  FileEventOperation,
  FileEventPayload,
  MemoryEventType,
  TokenUsageBreakdown,
} from '@/lib/types'

const id = z.string().min(1).max(512)
const routeId = z
  .string()
  .min(1)
  .max(128)
  .regex(/^[A-Za-z0-9_-]+$/)
const count = z.number().int().nonnegative()
const metric = z.number().nonnegative().max(Number.MAX_SAFE_INTEGER)
const title = z.string().min(1).max(512)
const date = z
  .string()
  .max(64)
  .refine((value) => Number.isFinite(Date.parse(value)))

function isRelativePath(path: string): boolean {
  return (
    !path.includes('\\') &&
    !/^[A-Za-z]:/.test(path) &&
    !Array.from(path).some((char) => char.charCodeAt(0) < 32) &&
    path.split('/').every((part) => part !== '' && part !== '.' && part !== '..')
  )
}

const relativePath = z.string().min(1).max(1024).refine(isRelativePath)

const operation = z.enum(['created', 'updated', 'deleted', 'failed'])
const canonicalUuid = z.string().regex(/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/)
const pointer = z.object({
  id: canonicalUuid,
  version_id: canonicalUuid,
  op: operation,
  public_message_id: id.optional(),
})

export function fileActivityPointer(content: unknown): {
  id: string
  version_id: string
  op: FileEventOperation
  public_message_id?: string
} | null {
  const parsed = pointer.safeParse(content)
  return parsed.success ? parsed.data : null
}

const fileEvent = z.object({
  op: operation,
  id: routeId,
  agent_id: routeId.nullable().default(null),
  conversation_id: routeId,
  assistant_msg_id: id,
  run_id: id,
  tool_call_id: id.nullish(),
  source_tool_name: title.nullish(),
  path: relativePath,
  display_name: title,
  mime_type: z.string().min(1).max(255),
  extension: z.string().max(128).nullish(),
  artifact_kind: z.enum([
    'image',
    'video',
    'audio',
    'pdf',
    'markdown',
    'html',
    'code',
    'document',
    'data',
    'cad',
    'other',
  ]),
  size_bytes: count,
  sha256: z.string().regex(/^[0-9a-f]{64}$/i),
  status: z.enum(['writing', 'ready', 'deleted', 'failed']),
  is_favorite: z.boolean(),
  last_opened_at: date.nullish(),
  preview_count: count,
  download_count: count,
  version_id: routeId,
  version_number: count,
  created_at: date,
  updated_at: date,
  agent_name: title.nullish(),
  conversation_title: title.nullish(),
  url: z.string().max(1024),
  preview_url: z.string().max(1024),
  download_url: z.string().max(1024),
  linked_message_ids: z.array(id).max(512).nullish(),
})

export function fileActivitySummary(
  content: unknown,
  conversationId?: string,
): FileEventPayload | null {
  const parsed = fileEvent.safeParse(content)
  if (!parsed.success) return null
  const file = parsed.data
  if (conversationId !== undefined && file.conversation_id !== conversationId) return null
  const url = `/api/conversations/${file.conversation_id}/artifacts/${file.id}`
  if (
    file.url !== url ||
    file.preview_url !== `${url}/content` ||
    file.download_url !== `${url}/download`
  ) {
    return null
  }
  return file
}

const usage = z.object({
  prompt_tokens: count,
  completion_tokens: count,
  cache_creation_tokens: count.default(0),
  cache_read_tokens: count.default(0),
  estimated_cost: metric.nullish(),
  ttft_ms: metric.nullish(),
  generation_ms: metric.nullish(),
  tokens_per_second: metric.nullish(),
})

export function usageActivityBreakdown(content: unknown): TokenUsageBreakdown | null {
  const parsed = usage.safeParse(content)
  if (!parsed.success) return null
  const { prompt_tokens, completion_tokens, cache_creation_tokens, cache_read_tokens } = parsed.data
  const result: TokenUsageBreakdown = {
    prompt_tokens,
    completion_tokens,
    cache_creation_tokens,
    cache_read_tokens,
  }
  // The saved REST row serializes unavailable timing and cost as null, whereas
  // a live activity omits them. Leave absent metrics out when combining a call.
  for (const key of ['estimated_cost', 'ttft_ms', 'generation_ms', 'tokens_per_second'] as const) {
    const value = parsed.data[key]
    if (value != null) result[key] = value
  }
  return result
}

const draft = z.object({
  session_id: id,
  mode: z.enum(['create', 'improve']),
  slug: title.nullable(),
  file_count: count,
  files: z.array(z.object({ path: relativePath, size: count })).max(512),
  changed_count: count,
  credential_requirement_count: count.default(0),
})

export function skillDraftActivity(content: unknown): SkillDraftBrief | null {
  const parsed = draft.safeParse(content)
  return parsed.success ? parsed.data : null
}

const issue = z.object({
  code: title,
  severity: z.enum(['error', 'warning', 'info']),
  message: z.string().max(4096).optional(),
  path: z
    .string()
    .max(1024)
    .refine((value) => isRelativePath(value.replace(/\/$/, '')))
    .nullish(),
})
const issues = z.array(issue).max(512)
const status = z.enum(['pass', 'warning', 'error'])
const counts = {
  error_count: count.optional(),
  warning_count: count.optional(),
  info_count: count.optional(),
}
const target = z.object({ status, issues: issues.optional() })
const compatibility = z.object({
  ...counts,
  status: status.optional(),
  targets: z.object({
    openai_codex: target.optional(),
    claude_code: target.optional(),
    vercel_agent_skills: target.optional(),
  }),
})
const validation = z.object({
  session_id: id.optional(),
  tool_name: z.enum(['validate_skill', 'finalize_skill']),
  validation_result: z.object({
    ...counts,
    valid: z.boolean(),
    issues,
    compatibility_result: compatibility.optional(),
  }),
})

export function skillValidationActivity(content: unknown): SkillValidationSnapshot | null {
  const parsed = validation.safeParse(content)
  return parsed.success ? parsed.data : null
}

const scope = z.enum(['agent', 'user'])
const memories = z.object({
  memories: z
    .array(
      z.object({
        id: id.nullish(),
        line_id: id.nullish(),
        scope,
        path: z.enum(['auto', 'tool', 'profile']).optional(),
      }),
    )
    .max(256),
})

export function recallActivity(content: unknown): RecalledMemoryBrief[] | null {
  const parsed = memories.safeParse(content)
  if (!parsed.success) return null
  return parsed.data.memories.map((entry) => ({
    id: entry.id ?? undefined,
    lineId: entry.line_id ?? undefined,
    scope: entry.scope,
    path: entry.path,
    content: '<redacted>',
  }))
}

const names = z.object({
  names: z.record(
    id.refine((key) => !Object.hasOwn(Object.prototype, key) && key !== 'prototype'),
    title,
  ),
})

export function subagentNamesActivity(content: unknown): Record<string, string> | null {
  const parsed = names.safeParse(content)
  if (!parsed.success || Object.keys(parsed.data.names).length > 256) return null
  return parsed.data.names
}

const memoryEvent = z.enum(['memory_proposed', 'memory_saved', 'memory_rejected', 'memory_deleted'])
const memoProposal = z.object({
  scope: z.literal('agent_memo'),
  id: canonicalUuid,
  content: z.literal('<redacted>'),
})

export function isAgentMemoProposal(content: unknown): boolean {
  return memoProposal.safeParse(content).success
}

const memoryChange = z.object({
  scope,
  id: id.nullish(),
  memory_id: id.nullish(),
  proposal_id: id.nullish(),
})

export function memoryChangeActivity(
  type: string,
  content: unknown,
): { name: MemoryEventType; id?: string } | null {
  if (!type.startsWith('moldy.')) return null
  const name = memoryEvent.safeParse(type.slice('moldy.'.length))
  const parsed = memoryChange.safeParse(content)
  if (!name.success || !parsed.success) return null
  return {
    name: name.data,
    id: parsed.data.memory_id ?? parsed.data.id ?? parsed.data.proposal_id ?? undefined,
  }
}
