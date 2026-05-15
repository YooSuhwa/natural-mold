'use client'

import { useState, type FormEvent } from 'react'
import Link from 'next/link'
import { EyeIcon, EyeOffIcon, Loader2Icon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

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
    void onSubmit(email, password)
  }

  return (
    <form
      onSubmit={handleSubmit}
      aria-busy={isLoading}
      noValidate
      className="space-y-4"
    >
      <header className="space-y-1.5">
        <h1 className="text-2xl font-semibold tracking-tight">{t('auth.login.title')}</h1>
        <p className="text-sm text-muted-foreground">{t('auth.login.subtitle')}</p>
      </header>

      {showCallbackNotice ? (
        <AuthAlert variant="info">{t('auth.login.expiredNotice')}</AuthAlert>
      ) : null}

      {mapped && mapped.field === null ? (
        <AuthAlert>{t(mapped.messageKey)}</AuthAlert>
      ) : null}

      <fieldset disabled={isLoading} className="contents">
        <div className="space-y-1.5">
          <label htmlFor="login-email" className="text-sm font-medium">
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
            aria-describedby={
              mapped?.field === 'email' ? 'login-email-error' : undefined
            }
          />
          {emailError ? (
            <p className="text-xs text-destructive">{t('auth.errors.invalidEmail')}</p>
          ) : mapped?.field === 'email' ? (
            <p id="login-email-error" className="text-xs text-destructive">
              {t(mapped.messageKey)}
            </p>
          ) : null}
        </div>

        <div className="space-y-1.5">
          <label htmlFor="login-password" className="text-sm font-medium">
            {t('auth.login.password')}
          </label>
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
              {showPassword ? (
                <EyeOffIcon className="size-4" />
              ) : (
                <EyeIcon className="size-4" />
              )}
            </button>
          </div>
          {passwordError ? (
            <p className="text-xs text-destructive">{t('auth.errors.passwordRequired')}</p>
          ) : null}
        </div>

        <div className="flex items-center justify-between text-sm">
          <Tooltip>
            <TooltipTrigger
              render={
                <label className="flex items-center gap-2 text-muted-foreground cursor-not-allowed">
                  <Checkbox checked disabled data-placeholder="true" />
                  <span>{t('auth.login.rememberMe')}</span>
                </label>
              }
            />
            <TooltipContent>{t('auth.login.rememberMeTooltip')}</TooltipContent>
          </Tooltip>
          <button
            type="button"
            aria-disabled
            className="text-primary-strong hover:underline disabled:opacity-60"
            onClick={(e) => {
              e.preventDefault()
            }}
          >
            {t('auth.login.forgotPassword')}
          </button>
        </div>

        <Button type="submit" className="w-full" disabled={isLoading}>
          {isLoading ? (
            <>
              <Loader2Icon className="mr-2 size-4 animate-spin" aria-hidden />
              {t('auth.login.submitting')}
            </>
          ) : (
            t('auth.login.submit')
          )}
        </Button>
      </fieldset>

      <div className="relative my-2" aria-hidden>
        <div className="border-t border-border/60" />
        <span className="absolute inset-0 -top-2.5 flex items-center justify-center">
          <span className="bg-card px-2 text-xs text-muted-foreground">
            {t('auth.login.or')}
          </span>
        </span>
      </div>

      <Tooltip>
        <TooltipTrigger
          render={
            <span className={cn('block w-full', 'cursor-not-allowed')}>
              <Button
                type="button"
                variant="outline"
                className="w-full opacity-60"
                disabled
              >
                {t('auth.login.googleButton')}
              </Button>
            </span>
          }
        />
        <TooltipContent>{t('auth.login.googleComingSoon')}</TooltipContent>
      </Tooltip>

      <p className="text-center text-sm text-muted-foreground">
        {t('auth.login.noAccount')}{' '}
        <Link href="/register" className="text-primary-strong hover:underline">
          {t('auth.login.registerLink')}
        </Link>
      </p>
    </form>
  )
}
