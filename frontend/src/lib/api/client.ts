const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8001'

class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  })

  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    const err = body.error ?? {}
    throw new ApiError(
      response.status,
      err.code ?? 'UNKNOWN_ERROR',
      err.message ?? body.detail ?? 'Unknown error',
    )
  }

  if (response.status === 204) return undefined as T
  return response.json()
}

export { API_BASE, ApiError }
