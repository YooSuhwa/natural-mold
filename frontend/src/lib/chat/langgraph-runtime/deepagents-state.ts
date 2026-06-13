export type DeepAgentTodoStatus = 'pending' | 'in_progress' | 'completed'

export interface DeepAgentTodo {
  readonly id: string
  readonly content: string
  readonly status: DeepAgentTodoStatus
}

export interface DeepAgentFile {
  readonly id: string
  readonly name: string
  readonly path: string
  readonly mimeType?: string
  readonly sizeBytes?: number
}

export interface DeepAgentsStateSnapshot {
  readonly todos: readonly DeepAgentTodo[]
  readonly files: readonly DeepAgentFile[]
}

interface DeepAgentsStateInput {
  readonly todos?: unknown
  readonly files?: unknown
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function textValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined
}

function numberValue(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function basename(path: string): string {
  const parts = path.split('/').filter(Boolean)
  return parts.at(-1) ?? path
}

function todoStatus(value: unknown): DeepAgentTodoStatus {
  const raw = textValue(value)?.toLowerCase()
  if (raw === 'completed' || raw === 'complete' || raw === 'done' || raw === 'success') {
    return 'completed'
  }
  if (raw === 'in_progress' || raw === 'running' || raw === 'active' || raw === 'started') {
    return 'in_progress'
  }
  return 'pending'
}

function todoContent(record: Record<string, unknown>, index: number): string | undefined {
  return (
    textValue(record.content) ??
    textValue(record.task) ??
    textValue(record.title) ??
    textValue(record.name) ??
    textValue(record.description) ??
    `Task ${index + 1}`
  )
}

function normalizeTodo(value: unknown, index: number): DeepAgentTodo | null {
  if (typeof value === 'string') {
    return { id: `todo-${index + 1}`, content: value, status: 'pending' }
  }
  if (!isRecord(value)) return null
  const content = todoContent(value, index)
  if (!content) return null
  return {
    id: textValue(value.id) ?? textValue(value.key) ?? `todo-${index + 1}`,
    content,
    status: todoStatus(value.status),
  }
}

function normalizeTodos(value: unknown): DeepAgentTodo[] {
  const items = Array.isArray(value) ? value : []
  return items
    .map((item, index) => normalizeTodo(item, index))
    .filter((item): item is DeepAgentTodo => item !== null)
}

function fileFromPath(path: string, metadata: unknown): DeepAgentFile {
  const record = isRecord(metadata) ? metadata : {}
  return {
    id: textValue(record.id) ?? `state-file:${path}`,
    name: textValue(record.display_name) ?? textValue(record.name) ?? basename(path),
    path,
    mimeType: textValue(record.mime_type) ?? textValue(record.mimeType),
    sizeBytes: numberValue(record.size_bytes) ?? numberValue(record.sizeBytes),
  }
}

function normalizeFile(value: unknown, index: number): DeepAgentFile | null {
  if (typeof value === 'string') return fileFromPath(value, {})
  if (!isRecord(value)) return null
  const path =
    textValue(value.path) ??
    textValue(value.file_path) ??
    textValue(value.filename) ??
    textValue(value.name) ??
    `file-${index + 1}`
  return fileFromPath(path, value)
}

function normalizeFiles(value: unknown): DeepAgentFile[] {
  if (Array.isArray(value)) {
    return value
      .map((item, index) => normalizeFile(item, index))
      .filter((item): item is DeepAgentFile => item !== null)
  }
  if (!isRecord(value)) return []
  return Object.entries(value).map(([path, metadata]) => fileFromPath(path, metadata))
}

export function selectDeepAgentsState(state: DeepAgentsStateInput): DeepAgentsStateSnapshot {
  return {
    todos: normalizeTodos(state.todos),
    files: normalizeFiles(state.files),
  }
}

export function hasDeepAgentsState(
  state: DeepAgentsStateSnapshot | undefined,
): state is DeepAgentsStateSnapshot {
  return Boolean(state && (state.todos.length > 0 || state.files.length > 0))
}
