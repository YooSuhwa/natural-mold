'use client'

import { useQuery } from '@tanstack/react-query'
import { CheckCircle2Icon, SparklesIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import Image from 'next/image'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useEffect, type ReactNode } from 'react'

import { SESSION_QUERY_KEY } from '@/lib/auth/session'
import { API_BASE } from '@/lib/api/client'
import type { User } from '@/lib/types/user'

const AUTH_STAR_COUNT = 5
const AUTH_AVATAR_COUNT = 3

export default function AuthLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname()
  const router = useRouter()
  const t = useTranslations()
  const isLogin = !pathname.includes('/register')
  const features = [
    t('auth.hero.features.noCode'),
    t('auth.hero.features.branching'),
    t('auth.hero.features.security'),
  ]

  // Raw fetch bypasses apiFetch/withAuthRetry so a 401 here does NOT trigger
  // fireSessionExpired() → queryClient.clear(). If we used useSession() instead,
  // the clear() would cancel the in-flight query, re-run it indefinitely, and
  // show a spurious "session expired" toast while already on the login page.
  const { data: user } = useQuery<User | null>({
    queryKey: SESSION_QUERY_KEY,
    queryFn: async (): Promise<User | null> => {
      const res = await fetch(`${API_BASE}/api/auth/me`, { credentials: 'include' })
      if (!res.ok) return null
      return (await res.json()) as User
    },
    staleTime: 5 * 60 * 1000,
    retry: false,
  })

  useEffect(() => {
    if (user) router.replace('/')
  }, [user, router])

  if (user) return null

  return (
    <div className="auth-shell">
      {/* Sparkle stars */}
      {Array.from({ length: AUTH_STAR_COUNT }).map((_, i) => (
        <SparklesIcon
          key={`star-${i}`}
          aria-hidden
          className={`auth-star auth-star-${i + 1}`}
          strokeWidth={2.2}
        />
      ))}

      {/* Top bar */}
      <header className="auth-topbar">
        <div className="auth-brand">
          <Image
            src="/logo.webp"
            alt="Moldy"
            width={30}
            height={30}
            className="auth-brand-logo size-[30px] shrink-0 object-contain"
            priority
          />
          <span className="auth-brand-text">{t('auth.hero.brand')}</span>
        </div>
      </header>

      {/* Main */}
      <main className="auth-main">
        {/* Hero — visible on large screens only */}
        <div className="auth-hero hidden lg:flex">
          <span className="auth-category">
            <span className="auth-category-dot" />
            {t('auth.hero.category')}
          </span>

          <h2 className="auth-heading auth-hero-title">
            {isLogin ? (
              <>
                {t('auth.hero.loginTitleLine1')}
                <br />
                {t('auth.hero.loginTitleLine2')}
              </>
            ) : (
              <>
                {t('auth.hero.registerTitleLine1')}
                <br />
                {t('auth.hero.registerTitleLine2')}
              </>
            )}
          </h2>

          <p className="auth-hero-copy">
            {isLogin ? t('auth.hero.loginSubtitle') : t('auth.hero.registerSubtitle')}
          </p>

          <div className="auth-feature-list">
            {features.map((text) => (
              <div key={text} className="auth-feature">
                <CheckCircle2Icon className="size-4 shrink-0 text-primary-strong" />
                {text}
              </div>
            ))}
          </div>

          <div className="auth-social-proof">
            <div className="auth-avatar-stack">
              {Array.from({ length: AUTH_AVATAR_COUNT }).map((_, i) => (
                <span key={`avatar-${i}`} aria-hidden className="auth-avatar-dot" />
              ))}
            </div>
            <span>
              {t.rich('auth.hero.socialProof', {
                strong: (chunks) => <strong className="auth-strong">{chunks}</strong>,
              })}
            </span>
          </div>
        </div>

        {/* Card column */}
        <div className="auth-card-column">
          <div className="auth-card-wrap">
            {/* Floating mascot */}
            <div aria-hidden className="auth-mascot">
              <div className="auth-mascot-halo" />
              <Image
                src="/moldy-mascot.webp"
                alt=""
                width={170}
                height={170}
                className="auth-mascot-image"
                draggable={false}
                priority
              />
            </div>

            {/* Card */}
            <div className="auth-card">
              {/* Tab switcher */}
              <div data-tab={isLogin ? 'login' : 'register'} role="tablist" className="auth-tabs">
                <span className="auth-tab-indicator" aria-hidden />
                <Link
                  href="/login"
                  role="tab"
                  aria-selected={isLogin}
                  data-active={isLogin}
                  className="auth-tab focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {t('auth.login.submit')}
                </Link>
                <Link
                  href="/register"
                  role="tab"
                  aria-selected={!isLogin}
                  data-active={!isLogin}
                  className="auth-tab focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {t('auth.register.submit')}
                </Link>
              </div>

              {children}
            </div>
          </div>
        </div>
      </main>

      <footer className="auth-footer">{t('auth.hero.copyright')}</footer>
    </div>
  )
}
