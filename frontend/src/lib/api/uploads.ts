import { apiFetch, apiUpload } from './client'
import type { ArtifactTextContent, UploadResponse } from '@/lib/types'

/** Upload a single file via multipart/form-data (cookie auth + CSRF). */
export async function uploadFile(file: File, signal?: AbortSignal): Promise<UploadResponse> {
  const fd = new FormData()
  fd.append('file', file)
  return apiUpload<UploadResponse>('/api/uploads', fd, signal)
}

/** Text body of a text-ish upload (markdown/json/csv/code/text previews). */
export function getUploadTextContent(uploadId: string): Promise<ArtifactTextContent> {
  return apiFetch<ArtifactTextContent>(`/api/uploads/${uploadId}/content`)
}
