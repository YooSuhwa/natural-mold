import { API_BASE, ApiError } from './client'
import type { UploadResponse } from '@/lib/types'

/**
 * Upload a single file via multipart/form-data. Bypasses ``apiFetch`` because
 * that helper sets ``Content-Type: application/json`` which would clobber the
 * multipart boundary.
 */
export async function uploadFile(file: File, signal?: AbortSignal): Promise<UploadResponse> {
  const fd = new FormData()
  fd.append('file', file)

  const response = await fetch(`${API_BASE}/api/uploads`, {
    method: 'POST',
    body: fd,
    signal,
  })

  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as {
      error?: { code?: string; message?: string }
      detail?: string
    }
    const err = body.error ?? {}
    throw new ApiError(
      response.status,
      err.code ?? 'UPLOAD_FAILED',
      err.message ?? body.detail ?? 'Upload failed',
    )
  }
  return response.json()
}
