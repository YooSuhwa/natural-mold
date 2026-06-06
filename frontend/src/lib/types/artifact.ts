export type ArtifactKind =
  | 'image'
  | 'video'
  | 'audio'
  | 'pdf'
  | 'markdown'
  | 'html'
  | 'code'
  | 'document'
  | 'data'
  | 'cad'
  | 'other'

export type ArtifactStatus = 'writing' | 'ready' | 'deleted' | 'failed'
export type FileEventOperation = 'created' | 'updated' | 'deleted' | 'failed'

export interface ArtifactSummary {
  id: string
  agent_id: string
  conversation_id: string
  assistant_msg_id: string
  run_id: string
  tool_call_id?: string | null
  source_tool_name?: string | null
  path: string
  display_name: string
  mime_type: string
  extension?: string | null
  artifact_kind: ArtifactKind
  size_bytes: number
  sha256: string
  status: ArtifactStatus
  is_favorite: boolean
  last_opened_at?: string | null
  preview_count: number
  download_count: number
  version_id: string
  version_number: number
  created_at: string
  updated_at: string
  agent_name?: string | null
  conversation_title?: string | null
  url: string
  preview_url: string
  download_url: string
}

export interface FileEventPayload extends ArtifactSummary {
  op: FileEventOperation
}

export interface ArtifactLibraryPage {
  items: ArtifactSummary[]
  next_cursor: string | null
  has_more: boolean
}

export interface ArtifactKindStat {
  kind: ArtifactKind
  count: number
  size_bytes: number
}

export interface ArtifactLibraryStats {
  total_count: number
  total_size_bytes: number
  favorite_count: number
  by_kind: ArtifactKindStat[]
  recent_count_7d: number
}

export interface ArtifactTextContent {
  text: string
  truncated: boolean
  mime_type: string
  size_bytes: number
}

export interface ArtifactLibraryParams {
  q?: string | null
  agent_id?: string | null
  conversation_id?: string | null
  kind?: ArtifactKind | null
  favorite?: boolean | null
  limit?: number
  cursor?: string | null
}
