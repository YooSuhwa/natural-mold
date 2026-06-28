import type { ArtifactKind, ArtifactSummary, MessageAttachmentBrief } from '@/lib/types'

/** filename → 확장자(소문자, 선행 점 제거). 확장자가 없으면 null. */
export function extensionFromFilename(filename: string): string | null {
  const dot = filename.lastIndexOf('.')
  if (dot <= 0 || dot === filename.length - 1) return null
  return filename.slice(dot + 1).toLowerCase()
}

/**
 * mime_type → ArtifactKind. 미리보기 레지스트리는 mime/extension을 우선으로
 * dispatch하지만, kind도 보조 매칭 키이므로 일관되게 채워 둔다.
 */
export function artifactKindFromMime(mimeType: string): ArtifactKind {
  const mime = mimeType.toLowerCase()
  if (mime.startsWith('image/')) return 'image'
  if (mime.startsWith('video/')) return 'video'
  if (mime.startsWith('audio/')) return 'audio'
  if (mime === 'application/pdf') return 'pdf'
  if (mime === 'text/markdown') return 'markdown'
  if (mime === 'text/html' || mime === 'application/xhtml+xml') return 'html'
  if (mime === 'application/json') return 'data'
  if (mime.startsWith('text/')) return 'code'
  return 'other'
}

/**
 * 보낸 메시지 첨부(``MessageAttachmentBrief``)를 기존 artifact 미리보기 레지스트리가
 * 소비할 수 있는 ``ArtifactSummary`` 형태로 매핑한다.
 *
 * - 첨부는 실제 conversation artifact가 아니므로 artifact 전용 식별자
 *   (agent_id/conversation_id/version 등)는 안전한 기본값으로 둔다.
 * - ``url``/``preview_url``/``download_url``은 모두 업로드 다운로드 URL
 *   (``/api/uploads/{id}``)을 가리킨다 — 이미지/PDF 프리뷰는 이 URL만으로 렌더된다.
 */
export function attachmentToArtifactSummary(att: MessageAttachmentBrief): ArtifactSummary {
  return {
    id: att.id,
    agent_id: '',
    conversation_id: '',
    assistant_msg_id: '',
    run_id: '',
    tool_call_id: null,
    source_tool_name: null,
    path: att.filename,
    display_name: att.filename,
    mime_type: att.mime_type,
    extension: extensionFromFilename(att.filename),
    artifact_kind: artifactKindFromMime(att.mime_type),
    size_bytes: att.size_bytes,
    sha256: '',
    status: 'ready',
    is_favorite: false,
    last_opened_at: null,
    preview_count: 0,
    download_count: 0,
    version_id: '',
    version_number: 0,
    created_at: '',
    updated_at: '',
    agent_name: null,
    conversation_title: null,
    url: att.url,
    preview_url: att.url,
    download_url: att.url,
  }
}
