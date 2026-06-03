'use client'

import { EyeIcon, EyeOffIcon, Loader2Icon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { useState, type FormEvent } from 'react'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'

import { mapAuthError } from './auth-errors'
import { AuthAlert } from './AuthAlert'
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
  const [showPassword, setShowPassword] = useState(false)
  const [agreed, setAgreed] = useState(false)
  const [touched, setTouched] = useState<Record<string, boolean>>({})

  const nameError = touched.name && name.trim().length === 0
  const emailError = touched.email && !email.includes('@')
  const passwordTooShort = touched.password && password.length > 0 && password.length < 8
  const agreedError = touched.agreed && !agreed

  const formInvalid =
    name.trim().length === 0 || !email.includes('@') || password.length < 8 || !agreed

  const mapped = error ? mapAuthError(error, 'register') : null

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setTouched({ name: true, email: true, password: true, agreed: true })
    if (formInvalid) return
    void onSubmit({ name: name.trim(), email, password }).catch(() => {
      // Mutation state renders the mapped auth error; avoid a dev overlay.
    })
  }

  return (
    <form onSubmit={handleSubmit} aria-busy={isLoading} noValidate>
      <header className="mb-5">
        <h1 className="mb-1.5 text-[22px] font-bold leading-tight text-foreground">
          {t('auth.register.formTitle')}
        </h1>
      </header>

      {mapped && mapped.field === null ? (
        <div className="mb-3.5">
          <AuthAlert>{t(mapped.messageKey)}</AuthAlert>
        </div>
      ) : null}

      <fieldset disabled={isLoading} className="contents">
        <div className="grid gap-3.5">
          {/* Name */}
          <div>
            <label
              htmlFor="reg-name"
              className="mb-1.5 block text-sm font-medium"
            >
              {t('auth.register.name')}
            </label>
            <Input
              id="reg-name"
              type="text"
              autoComplete="name"
              required
              maxLength={80}
              placeholder={t('auth.register.namePlaceholder')}
              value={name}
              onChange={(e) => setName(e.target.value)}
              onBlur={() => setTouched((p) => ({ ...p, name: true }))}
              aria-invalid={nameError || mapped?.field === 'name' || undefined}
            />
            {nameError ? (
              <p className="mt-1.5 text-xs text-destructive">
                {t('auth.errors.nameRequired')}
              </p>
            ) : null}
          </div>

          {/* Email */}
          <div>
            <label
              htmlFor="reg-email"
              className="mb-1.5 block text-sm font-medium"
            >
              {t('auth.register.email')}
            </label>
            <Input
              id="reg-email"
              type="email"
              autoComplete="email"
              inputMode="email"
              required
              placeholder={t('auth.register.emailPlaceholder')}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              onBlur={() => setTouched((p) => ({ ...p, email: true }))}
              aria-invalid={emailError || mapped?.field === 'email' || undefined}
              aria-describedby={mapped?.field === 'email' ? 'reg-email-error' : undefined}
            />
            {emailError ? (
              <p className="mt-1.5 text-xs text-destructive">
                {t('auth.errors.invalidEmail')}
              </p>
            ) : mapped?.field === 'email' ? (
              <p
                id="reg-email-error"
                className="mt-1.5 text-xs text-destructive"
              >
                {t(mapped.messageKey)}
              </p>
            ) : null}
          </div>

          {/* Password */}
          <div>
            <label
              htmlFor="reg-password"
              className="mb-1.5 block text-sm font-medium"
            >
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
                aria-label={
                  showPassword ? t('auth.password.hide') : t('auth.password.show')
                }
                onClick={() => setShowPassword((v) => !v)}
                className="absolute inset-y-0 right-2 flex items-center text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
              >
                {showPassword ? <EyeOffIcon className="size-4" /> : <EyeIcon className="size-4" />}
              </button>
            </div>
            <PasswordStrengthMeter password={password} />
            {passwordTooShort ? (
              <p className="mt-1 text-xs text-destructive">
                {t('auth.register.strength.tooShort')}
              </p>
            ) : null}
          </div>

          {/* Terms */}
          <div>
            <label className="flex items-start gap-2.5 text-sm cursor-pointer">
              <Checkbox
                checked={agreed}
                onCheckedChange={(v) => setAgreed(Boolean(v))}
                className="mt-0.5 shrink-0"
                aria-invalid={agreedError || undefined}
              />
              <span className="text-muted-foreground leading-relaxed">
                {t('auth.register.terms')}
              </span>
            </label>
            {agreedError ? (
              <p
                className="mt-1.5 pl-[26px] text-xs text-destructive"
              >
                {t('auth.register.termsRequired')}
              </p>
            ) : null}
          </div>

          {/* Submit */}
          <Button
            type="submit"
            className="mt-0.5 w-full gap-2"
            disabled={isLoading || formInvalid}
          >
            {isLoading ? (
              <>
                <Loader2Icon className="size-4 animate-spin" aria-hidden />
                {t('auth.register.submitting')}
              </>
            ) : (
              t('auth.register.submit')
            )}
          </Button>
        </div>
      </fieldset>
    </form>
  )
}
