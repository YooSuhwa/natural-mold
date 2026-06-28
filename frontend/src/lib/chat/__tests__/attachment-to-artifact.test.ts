import { describe, expect, it } from 'vitest'
import {
  artifactKindFromMime,
  attachmentToArtifactSummary,
  extensionFromFilename,
} from '../attachment-to-artifact'
import type { MessageAttachmentBrief } from '@/lib/types'

function brief(overrides: Partial<MessageAttachmentBrief> = {}): MessageAttachmentBrief {
  return {
    id: 'upload-1',
    filename: 'photo.png',
    mime_type: 'image/png',
    size_bytes: 1234,
    url: '/api/uploads/upload-1',
    ...overrides,
  }
}

describe('extensionFromFilename', () => {
  it('returns the lowercased extension without the dot', () => {
    expect(extensionFromFilename('Report.PDF')).toBe('pdf')
    expect(extensionFromFilename('archive.tar.gz')).toBe('gz')
  })

  it('returns null when there is no usable extension', () => {
    expect(extensionFromFilename('README')).toBeNull()
    expect(extensionFromFilename('.gitignore')).toBeNull()
    expect(extensionFromFilename('trailingdot.')).toBeNull()
  })
})

describe('artifactKindFromMime', () => {
  it('maps common mime types to artifact kinds', () => {
    expect(artifactKindFromMime('image/jpeg')).toBe('image')
    expect(artifactKindFromMime('application/pdf')).toBe('pdf')
    expect(artifactKindFromMime('text/markdown')).toBe('markdown')
    expect(artifactKindFromMime('text/html')).toBe('html')
    expect(artifactKindFromMime('application/json')).toBe('data')
    expect(artifactKindFromMime('text/plain')).toBe('code')
    expect(artifactKindFromMime('audio/mpeg')).toBe('audio')
    expect(artifactKindFromMime('video/mp4')).toBe('video')
  })

  it('falls back to "other" for unknown mime types', () => {
    expect(artifactKindFromMime('application/octet-stream')).toBe('other')
  })
})

describe('attachmentToArtifactSummary', () => {
  it('maps an image attachment onto an ArtifactSummary the registry can dispatch', () => {
    const artifact = attachmentToArtifactSummary(brief())

    expect(artifact.id).toBe('upload-1')
    expect(artifact.display_name).toBe('photo.png')
    expect(artifact.mime_type).toBe('image/png')
    expect(artifact.extension).toBe('png')
    expect(artifact.artifact_kind).toBe('image')
    expect(artifact.size_bytes).toBe(1234)
    // 모든 URL 필드는 업로드 다운로드 URL을 가리킨다.
    expect(artifact.url).toBe('/api/uploads/upload-1')
    expect(artifact.preview_url).toBe('/api/uploads/upload-1')
    expect(artifact.download_url).toBe('/api/uploads/upload-1')
    // artifact 전용 식별자는 안전한 기본값.
    expect(artifact.status).toBe('ready')
    expect(artifact.sha256).toBe('')
    expect(artifact.version_number).toBe(0)
    expect(artifact.is_favorite).toBe(false)
  })

  it('derives extension + kind for non-image files', () => {
    const artifact = attachmentToArtifactSummary(
      brief({ filename: 'notes.pdf', mime_type: 'application/pdf' }),
    )

    expect(artifact.extension).toBe('pdf')
    expect(artifact.artifact_kind).toBe('pdf')
  })

  it('tolerates a filename without an extension', () => {
    const artifact = attachmentToArtifactSummary(
      brief({ filename: 'LICENSE', mime_type: 'text/plain' }),
    )

    expect(artifact.extension).toBeNull()
    expect(artifact.artifact_kind).toBe('code')
  })
})
