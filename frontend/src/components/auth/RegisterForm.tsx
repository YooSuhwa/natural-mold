'use client'

import { useState, type FormEvent } from 'react'
import Link from 'next/link'
import { EyeIcon, EyeOffIcon, Loader2Icon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'

import { AuthAlert } from './AuthAlert'
import { mapAuthError } from './auth-errors'
import { PasswordStrengthMeter } from './PasswordStrengthMeter'

interface Props {
  onSubmit: (data: { email: string; password: string; name: string }) => Promise<void>
  isLoading: boolean
  error: unknown
}

export function RegisterForm({ onSubmit, isLoading, error }: Props) {
  const t = useTranslations()
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [passwordConfirm, setPasswordConfirm] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [agreed, setAgreed] = useState(false)
  const [touched, setTouched] = useState<Record<string, boolean>>({})

  const nameError = touched.name && name.trim().length === 0
  const emailError = touched.email && !email.includes('@')
  const passwordTooShort = touched.password && password.length > 0 && password.length < 8
  const mismatch =
    touched.passwordConfirm && passwordConfirm.length > 0 && password !== passwordConfirm

  const formInvalid =
    name.trim().length === 0 ||
    !email.includes('@') ||
    password.length < 8 ||
    password !== passwordConfirm ||
    !agreed

  const mapped = error ? mapAuthError(error, 'register') : null

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setTouched({ name: true, email: true, password: true, passwordConfirm: true })
    if (formInvalid) return
    void onSubmit({ name: name.trim(), email, password })
  }

  return (
    <form
      onSubmit={handleSubmit}
      aria-busy={isLoading}
      noValidate
      className="space-y-4"
    >
      <header className="space-y-1.5">
        <h1 className="text-2xl font-semibold tracking-tight">{t('auth.register.title')}</h1>
        <p className="text-sm text-muted-foreground">{t('auth.register.subtitle')}</p>
      </header>

      {mapped && mapped.field === null ? (
        <AuthAlert>{t(mapped.messageKey)}</AuthAlert>
      ) : null}

      <fieldset disabled={isLoading} className="contents">
        <div className="space-y-1.5">
          <label htmlFor="reg-name" className="text-sm font-medium">
            {t('auth.register.name')}
          </label>
          <Input
            id="reg-name"
            type="text"
            autoComplete="name"
            required
            maxLength={80}
            placeholder="홍길동"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onBlur={() => setTouched((p) => ({ ...p, name: true }))}
            aria-invalid={nameError || mapped?.field === 'name' || undefined}
          />
          {nameError ? (
            <p className="text-xs text-destructive">{t('auth.errors.nameRequired')}</p>
          ) : null}
        </div>

        <div className="space-y-1.5">
          <label htmlFor="reg-email" className="text-sm font-medium">
            {t('auth.register.email')}
          </label>
          <Input
            id="reg-email"
            type="email"
            autoComplete="email"
            inputMode="email"
            required
            placeholder="you@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onBlur={() => setTouched((p) => ({ ...p, email: true }))}
            aria-invalid={emailError || mapped?.field === 'email' || undefined}
            aria-describedby={mapped?.field === 'email' ? 'reg-email-error' : undefined}
          />
          {emailError ? (
            <p className="text-xs text-destructive">{t('auth.errors.invalidEmail')}</p>
          ) : mapped?.field === 'email' ? (
            <p id="reg-email-error" className="text-xs text-destructive">
              {t(mapped.messageKey)}
            </p>
          ) : null}
        </div>

        <div className="space-y-1.5">
          <label htmlFor="reg-password" className="text-sm font-medium">
            {t('auth.register.password')}
          </label>
          <div className="relative">
            <Input
              id="reg-password"
              type={showPassword ? 'text' : 'password'}
              autoComplete="new-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onBlur={() => setTouched((p) => ({ ...p, password: true }))}
              aria-invalid={passwordTooShort || undefined}
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
          <PasswordStrengthMeter password={password} />
        </div>

        <div className="space-y-1.5">
          <label htmlFor="reg-password-confirm" className="text-sm font-medium">
            {t('auth.register.passwordConfirm')}
          </label>
          <Input
            id="reg-password-confirm"
            type={showPassword ? 'text' : 'password'}
            autoComplete="new-password"
            required
            value={passwordConfirm}
            onChange={(e) => setPasswordConfirm(e.target.value)}
            onBlur={() => setTouched((p) => ({ ...p, passwordConfirm: true }))}
            aria-invalid={mismatch || undefined}
          />
          {mismatch ? (
            <p className="text-xs text-destructive">{t('auth.register.mismatch')}</p>
          ) : null}
        </div>

        <label className="flex items-start gap-2 text-sm">
          <Checkbox
            checked={agreed}
            onCheckedChange={(v) => setAgreed(Boolean(v))}
            className="mt-0.5"
          />
          <span className="text-muted-foreground">{t('auth.register.terms')}</span>
        </label>

        <Button type="submit" className="w-full" disabled={isLoading || formInvalid}>
          {isLoading ? (
            <>
              <Loader2Icon className="mr-2 size-4 animate-spin" aria-hidden />
              {t('auth.register.submitting')}
            </>
          ) : (
            t('auth.register.submit')
          )}
        </Button>
      </fieldset>

      <p className="text-center text-sm text-muted-foreground">
        {t('auth.register.haveAccount')}{' '}
        <Link href="/login" className="text-primary-strong hover:underline">
          {t('auth.register.loginLink')}
        </Link>
      </p>
    </form>
  )
}
