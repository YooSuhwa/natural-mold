import type { ArtifactKind } from '@/lib/types'

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
  readonly artifactKind?: ArtifactKind
  readonly previewUrl?: string
  readonly downloadUrl?: string
  readonly content?: string
}

export interface DeepAgentsStateSnapshot {
  readonly todos: readonly DeepAgentTodo[]
  readonly files: readonly DeepAgentFile[]
}

interface DeepAgentsStateInput {
  readonly todos?: unknown
  readonly files?: unknown
  readonly artifacts?: unknown
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

function artifactKindValue(value: unknown): ArtifactKind | undefined {
  const raw = textValue(value)
  switch (raw) {
    case 'image':
    case 'video':
    case 'audio':
    case 'pdf':
    case 'markdown':
    case 'html':
    case 'code':
    case 'document':
    case 'data':
    case 'cad':
    case 'other':
      return raw
    default:
      return undefined
  }
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
    id: textValue(record.id) ?? textValue(record.artifact_id) ?? `state-file:${path}`,
    name:
      textValue(record.display_name) ??
      textValue(record.displayName) ??
      textValue(record.name) ??
      basename(path),
    path,
    mimeType: textValue(record.mime_type) ?? textValue(record.mimeType),
    sizeBytes: numberValue(record.size_bytes) ?? numberValue(record.sizeBytes),
    artifactKind: artifactKindValue(record.artifact_kind) ?? artifactKindValue(record.artifactKind),
    previewUrl: textValue(record.preview_url) ?? textValue(record.previewUrl),
    downloadUrl: textValue(record.download_url) ?? textValue(record.downloadUrl),
    content: textValue(record.content) ?? textValue(metadata),
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

function fileMatches(left: DeepAgentFile, right: DeepAgentFile): boolean {
  return left.id === right.id || left.path === right.path
}

function mergeFile(left: DeepAgentFile, right: DeepAgentFile): DeepAgentFile {
  return {
    id: left.id,
    name: left.name,
    path: left.path,
    mimeType: left.mimeType ?? right.mimeType,
    sizeBytes: left.sizeBytes ?? right.sizeBytes,
    artifactKind: left.artifactKind ?? right.artifactKind,
    previewUrl: left.previewUrl ?? right.previewUrl,
    downloadUrl: left.downloadUrl ?? right.downloadUrl,
    content: left.content ?? right.content,
  }
}

function dedupeFiles(files: readonly DeepAgentFile[]): DeepAgentFile[] {
  const merged: DeepAgentFile[] = []
  for (const file of files) {
    const existingIndex = merged.findIndex((item) => fileMatches(item, file))
    if (existingIndex === -1) {
      merged.push(file)
      continue
    }
    const existing = merged[existingIndex]
    if (existing) merged[existingIndex] = mergeFile(existing, file)
  }
  return merged
}

export function selectDeepAgentsState(state: DeepAgentsStateInput): DeepAgentsStateSnapshot {
  return {
    todos: normalizeTodos(state.todos),
    files: dedupeFiles([...normalizeFiles(state.artifacts), ...normalizeFiles(state.files)]),
  }
}

export function hasDeepAgentsState(
  state: DeepAgentsStateSnapshot | undefined,
): state is DeepAgentsStateSnapshot {
  return Boolean(state && (state.todos.length > 0 || state.files.length > 0))
}
