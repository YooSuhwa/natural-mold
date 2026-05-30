'use client'

import { EyeIcon, EyeOffIcon, Loader2Icon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { useState, type FormEvent } from 'react'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

import { AuthAlert } from './AuthAlert'
import { mapAuthError } from './auth-errors'

interface Props {
  onSubmit: (email: string, password: string) => Promise<void>
  isLoading: boolean
  error: unknown
  defaultEmail?: string
  showCallbackNotice?: boolean
}

export function LoginForm({
  onSubmit,
  isLoading,
  error,
  defaultEmail = '',
  showCallbackNotice,
}: Props) {
  const t = useTranslations()
  const [email, setEmail] = useState(defaultEmail)
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [touched, setTouched] = useState<{ email?: boolean; password?: boolean }>({})

  const emailError = touched.email && !email.includes('@')
  const passwordError = touched.password && password.length === 0
  const mapped = error ? mapAuthError(error, 'login') : null

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setTouched({ email: true, password: true })
    if (!email.includes('@') || password.length === 0) return
    void onSubmit(email, password).catch(() => {
      // Mutation state renders the mapped auth error; avoid a dev overlay.
    })
  }

  return (
    <form onSubmit={handleSubmit} aria-busy={isLoading} noValidate>
      <header style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, letterSpacing: '-0.025em', margin: '0 0 6px' }}>
          {t('auth.login.formTitle')}
        </h1>
        {/* <p style={{ fontSize: 13.5, color: 'oklch(0.556 0 0)', margin: 0 }}>
          이메일과 비밀번호로 계속하기
        </p> */}
      </header>

      {showCallbackNotice ? (
        <div style={{ marginBottom: 14 }}>
          <AuthAlert variant="info">{t('auth.login.expiredNotice')}</AuthAlert>
        </div>
      ) : null}

      {mapped && mapped.field === null ? (
        <div style={{ marginBottom: 14 }}>
          <AuthAlert>{t(mapped.messageKey)}</AuthAlert>
        </div>
      ) : null}

      <fieldset disabled={isLoading} className="contents">
        <div style={{ display: 'grid', gap: 13 }}>
          {/* Email */}
          <div>
            <label
              htmlFor="login-email"
              className="text-sm font-medium"
              style={{ display: 'block', marginBottom: 6 }}
            >
              {t('auth.login.email')}
            </label>
            <Input
              id="login-email"
              type="email"
              autoComplete="email"
              inputMode="email"
              autoFocus
              required
              placeholder={t('auth.login.emailPlaceholder')}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              onBlur={() => setTouched((prev) => ({ ...prev, email: true }))}
              aria-invalid={emailError || mapped?.field === 'email' || undefined}
            />
            {emailError ? (
              <p style={{ fontSize: 12, color: 'oklch(0.577 0.245 27.325)', marginTop: 6 }}>
                {t('auth.errors.invalidEmail')}
              </p>
            ) : mapped?.field === 'email' ? (
              <p style={{ fontSize: 12, color: 'oklch(0.577 0.245 27.325)', marginTop: 6 }}>
                {t(mapped.messageKey)}
              </p>
            ) : null}
          </div>

          {/* Password */}
          <div>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'baseline',
                marginBottom: 6,
              }}
            >
              <label htmlFor="login-password" className="text-sm font-medium">
                {t('auth.login.password')}
              </label>
              <button
                type="button"
                aria-disabled
                className="text-xs hover:underline opacity-60 cursor-not-allowed"
                style={{
                  color: 'oklch(0.596 0.145 163.225)',
                  background: 'none',
                  border: 'none',
                  padding: 0,
                }}
                onClick={(e) => e.preventDefault()}
              >
                {t('auth.login.forgotPassword')}
              </button>
            </div>
            <div className="relative">
              <Input
                id="login-password"
                type={showPassword ? 'text' : 'password'}
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onBlur={() => setTouched((prev) => ({ ...prev, password: true }))}
                aria-invalid={passwordError || undefined}
                className="pr-9"
              />
              <button
                type="button"
                aria-pressed={showPassword}
                aria-label={
                  showPassword ? t('auth.password.hide') : t('auth.password.show')
                }
                onClick={() => setShowPassword((v) => !v)}
                className="absolute inset-y-0 right-2 flex items-center text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
              >
                {showPassword ? <EyeOffIcon className="size-4" /> : <EyeIcon className="size-4" />}
              </button>
            </div>
            {passwordError ? (
              <p style={{ fontSize: 12, color: 'oklch(0.577 0.245 27.325)', marginTop: 6 }}>
                {t('auth.errors.passwordRequired')}
              </p>
            ) : null}
          </div>

          {/* Remember me */}
          <Tooltip>
            <TooltipTrigger
              render={
                <label className="flex items-center gap-2 text-muted-foreground cursor-not-allowed select-none text-sm">
                  <Checkbox checked disabled data-placeholder="true" />
                  <span>{t('auth.login.rememberMe')}</span>
                </label>
              }
            />
            <TooltipContent>{t('auth.login.rememberMeTooltip')}</TooltipContent>
          </Tooltip>

          {/* Submit */}
          <Button
            type="submit"
            className="w-full gap-2"
            disabled={isLoading}
            style={{ marginTop: 2 }}
          >
            {isLoading ? (
              <>
                <Loader2Icon className="size-4 animate-spin" aria-hidden />
                {t('auth.login.submitting')}
              </>
            ) : (
              t('auth.login.submit')
            )}
          </Button>
        </div>
      </fieldset>
    </form>
  )
}
