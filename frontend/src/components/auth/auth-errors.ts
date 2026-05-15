import { ApiError } from '@/lib/api/client'

export type AuthErrorScope = 'login' | 'register'

export interface FieldError {
  field: 'email' | 'password' | 'name' | 'passwordConfirm' | null
  /** i18n key under `auth.errors.*` or `auth.register.*`. */
  messageKey: string
}

/**
 * Map an ApiError → form-level + field-level message keys.
 *
 * Returns a single FieldError with `field === null` for form-level alerts.
 * The component decides where to render based on `field`.
 */
export function mapAuthError(err: unknown, scope: AuthErrorScope): FieldError {
  if (!(err instanceof ApiError)) {
    return { field: null, messageKey: 'auth.errors.network' }
  }

  // 422 validation_error — best-effort field detection from code/message.
  if (err.status === 422) {
    const msg = err.message.toLowerCase()
    if (msg.includes('password')) {
      return { field: 'password', messageKey: 'auth.register.strength.tooShort' }
    }
    if (msg.includes('email')) {
      return { field: 'email', messageKey: 'auth.errors.invalidEmail' }
    }
    if (msg.includes('name')) {
      return { field: 'name', messageKey: 'auth.errors.nameRequired' }
    }
  }

  switch (err.status) {
    case 401:
      return { field: null, messageKey: 'auth.errors.invalidCredentials' }
    case 403:
      return { field: null, messageKey: 'auth.errors.accountInactive' }
    case 409:
      return scope === 'register'
        ? { field: 'email', messageKey: 'auth.errors.emailTaken' }
        : { field: null, messageKey: 'auth.errors.serverError' }
    case 423:
      return { field: null, messageKey: 'auth.errors.accountLocked' }
    case 429:
      return { field: null, messageKey: 'auth.errors.rateLimit' }
    default:
      if (err.status >= 500) return { field: null, messageKey: 'auth.errors.serverError' }
      return { field: null, messageKey: 'auth.errors.serverError' }
  }
}
