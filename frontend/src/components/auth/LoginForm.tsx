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

function GoogleColorIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden>
      <path
        fill="#4285F4"
        d="M23.49 12.27c0-.79-.07-1.54-.19-2.27H12v4.51h6.44c-.28 1.46-1.11 2.69-2.36 3.52v2.94h3.81c2.23-2.06 3.6-5.1 3.6-8.7z"
      />
      <path
        fill="#34A853"
        d="M12 24c3.24 0 5.95-1.08 7.94-2.91l-3.81-2.94c-1.05.71-2.4 1.14-4.13 1.14-3.18 0-5.87-2.14-6.83-5.02H1.24v3.04C3.22 21.3 7.31 24 12 24z"
      />
      <path
        fill="#FBBC05"
        d="M5.17 14.27a7.26 7.26 0 0 1-.39-2.27c0-.79.14-1.55.39-2.27V6.69H1.24A11.96 11.96 0 0 0 0 12c0 1.94.47 3.77 1.24 5.31l3.93-3.04z"
      />
      <path
        fill="#EA4335"
        d="M12 4.74c1.79 0 3.39.62 4.66 1.82l3.38-3.38C17.95 1.23 15.24 0 12 0 7.31 0 3.22 2.7 1.24 6.69l3.93 3.04C6.13 6.88 8.82 4.74 12 4.74z"
      />
    </svg>
  )
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
    void onSubmit(email, password)
  }

  return (
    <form onSubmit={handleSubmit} aria-busy={isLoading} noValidate>
      <header style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, letterSpacing: '-0.025em', margin: '0 0 6px' }}>
          계정에 로그인
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

      {/* Google button */}
      <Tooltip>
        <TooltipTrigger
          render={<span className="block w-full cursor-not-allowed" style={{ marginBottom: 12 }} />}
        >
          <Button type="button" variant="outline" className="w-full opacity-60 gap-2.5" disabled>
            <GoogleColorIcon />
            Google로 계속하기
          </Button>
        </TooltipTrigger>
        <TooltipContent>{t('auth.login.googleComingSoon')}</TooltipContent>
      </Tooltip>

      {/* Divider */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, margin: '14px 0' }}>
        <div style={{ flex: 1, height: 1, background: 'oklch(0.922 0 0)' }} />
        <span style={{ fontSize: 12, color: 'oklch(0.556 0 0)' }}>또는 이메일로</span>
        <div style={{ flex: 1, height: 1, background: 'oklch(0.922 0 0)' }} />
      </div>

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
              placeholder="you@example.com"
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
                aria-label={showPassword ? '비밀번호 숨김' : '비밀번호 표시'}
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
