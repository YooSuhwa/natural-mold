const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8001"

class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
  }
}

export async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    throw new ApiError(response.status, error.detail || error.message || "Unknown error")
  }

  if (response.status === 204) return undefined as T
  return response.json()
}

export { API_BASE, ApiError }
