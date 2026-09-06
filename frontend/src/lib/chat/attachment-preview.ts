import type { ArtifactTextContent } from '@/lib/types'

/** Loads upload text only when a text attachment preview opens. */
export async function getAttachmentTextPreview(uploadId: string): Promise<ArtifactTextContent> {
  const { getUploadTextContent } = await import('@/lib/api/uploads')
  return getUploadTextContent(uploadId)
}
