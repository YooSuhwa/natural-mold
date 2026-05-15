/**
 * User type — mirrors backend `UserResponse` (app/schemas/auth.py).
 *
 * Returned by `/api/auth/me`, `/api/auth/login`, `/api/auth/register`.
 */
export interface User {
  id: string
  email: string
  name: string
  is_super_user: boolean
  is_active?: boolean
  created_at: string
  last_login_at?: string | null
}

export interface AuthResponse {
  user: User
  csrf_token: string
}

export interface RefreshResponse {
  csrf_token: string
}

export interface AuthErrorDetail {
  code: string
  message: string
}
