import { apiUpload } from './client'
import type { UploadResponse } from '@/lib/types'

/** Upload a single file via multipart/form-data (cookie auth + CSRF). */
export async function uploadFile(file: File, signal?: AbortSignal): Promise<UploadResponse> {
  const fd = new FormData()
  fd.append('file', file)
  return apiUpload<UploadResponse>('/api/uploads', fd, signal)
}
