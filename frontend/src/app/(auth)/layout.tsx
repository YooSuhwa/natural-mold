'use client'

import { useQuery } from '@tanstack/react-query'
import { CheckCircle2Icon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import Image from 'next/image'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useEffect, type CSSProperties, type ReactNode } from 'react'

import { SESSION_QUERY_KEY } from '@/lib/auth/session'
import type { User } from '@/lib/types/user'

const FEATURES = [
  '대화로 만드는 노코드 에이전트',
  'LangGraph 분기 · 시간여행 지원',
  '암호화된 API 키 · 안전한 도구 호출',
]

const AVATAR_COLORS = ['oklch(0.596 0.145 163.225)', 'oklch(0.78 0.15 75)', 'oklch(0.7 0.15 240)']

const STAR_PATH = 'M12 1 L13.5 9.5 L22 11 L13.5 12.5 L12 21 L10.5 12.5 L2 11 L10.5 9.5 Z'

type Sparkle = {
  top: string
  left: string
  size: number
  color: string
  animation: string
}

const SPARKLES: Sparkle[] = [
  {
    top: '18%',
    left: '14%',
    size: 14,
    color: 'oklch(0.75 0.13 163)',
    animation: 'auth-twinkle-1 5s ease-in-out infinite',
  },
  {
    top: '62%',
    left: '40%',
    size: 10,
    color: 'oklch(0.82 0.14 80)',
    animation: 'auth-twinkle-2 6.5s ease-in-out infinite',
  },
  {
    top: '22%',
    left: '74%',
    size: 18,
    color: 'oklch(0.75 0.13 163)',
    animation: 'auth-twinkle-3 7s ease-in-out infinite',
  },
  {
    top: '78%',
    left: '10%',
    size: 12,
    color: 'oklch(0.82 0.14 80)',
    animation: 'auth-twinkle-1 5.5s ease-in-out infinite',
  },
  {
    top: '88%',
    left: '52%',
    size: 16,
    color: 'oklch(0.75 0.13 163)',
    animation: 'auth-twinkle-2 4.5s ease-in-out infinite',
  },
]

type Bubble = { top: string; left: string; size: number; animation: string }

const BUBBLES: Bubble[] = [
  { top: '12%', left: '28%', size: 18, animation: 'auth-drift1 11s ease-in-out infinite' },
  { top: '68%', left: '82%', size: 22, animation: 'auth-drift2 13s ease-in-out infinite' },
  { top: '30%', left: '46%', size: 14, animation: 'auth-drift1 9s ease-in-out infinite' },
]

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8001'

export default function AuthLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname()
  const router = useRouter()
  const t = useTranslations()
  const isLogin = !pathname.includes('/register')

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
    <>
      <style>{`
        @keyframes auth-float-mascot {
          0%, 100% { transform: rotate(6deg) translateY(0); }
          50% { transform: rotate(6deg) translateY(-5px); }
        }
        @keyframes auth-drift1 { 0%,100%{transform:translate(0,0)} 50%{transform:translate(-6px,-10px)} }
        @keyframes auth-drift2 { 0%,100%{transform:translate(0,0)} 50%{transform:translate(8px,-6px)} }
        @keyframes auth-twinkle-1 { 0%,100%{opacity:.4;transform:scale(.9)} 50%{opacity:1;transform:scale(1.05)} }
        @keyframes auth-twinkle-2 { 0%,100%{opacity:.55;transform:scale(1)} 50%{opacity:.95;transform:scale(1.1)} }
        @keyframes auth-twinkle-3 { 0%,100%{opacity:.45;transform:scale(.95)} 50%{opacity:1;transform:scale(1)} }
        .auth-tab-indicator {
          position: absolute; top: 3px; bottom: 3px; left: 3px;
          width: calc(50% - 3px);
          background: #fff;
          border-radius: 7px;
          box-shadow: 0 1px 2px oklch(0 0 0 / 0.06), 0 1px 1px oklch(0 0 0 / 0.04);
          transition: transform .25s cubic-bezier(.2,.7,.2,1);
        }
        [data-tab="register"] .auth-tab-indicator { transform: translateX(100%); }

        .dark .auth-shell {
          background: linear-gradient(135deg, oklch(0.18 0.01 200) 0%, oklch(0.2 0.04 163) 60%, oklch(0.16 0.02 200) 100%) !important;
        }
        .dark .auth-card {
          background: oklch(0.22 0.01 163) !important;
          border-color: oklch(0.3 0.012 163) !important;
          box-shadow: 0 24px 60px -24px oklch(0 0 0 / 0.55), 0 1px 0 oklch(1 0 0 / 0.04) inset !important;
        }
        .dark .auth-card .auth-tab-indicator { background: oklch(0.3 0.015 163); }
        .dark .auth-halo { opacity: .6; }
        .dark .auth-star { opacity: .7; }
        .dark .auth-bubble { opacity: .55; }
        .dark .auth-mascot-halo { opacity: .35; }
        .dark .auth-muted { color: oklch(0.7 0.01 200) !important; }
        .dark .auth-strong { color: oklch(0.95 0.01 163) !important; }
        .dark .auth-heading { color: oklch(0.97 0.01 163) !important; }
        .dark .auth-link { color: oklch(0.78 0.12 163) !important; }
      `}</style>

      <div
        className="auth-shell"
        style={{
          minHeight: '100vh',
          background:
            'linear-gradient(135deg, oklch(0.985 0.008 100) 0%, oklch(0.965 0.045 163) 60%, oklch(0.975 0.025 200) 100%)',
          position: 'relative',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        {/* Ambient halos */}
        <div
          aria-hidden
          className="auth-halo"
          style={{
            position: 'fixed',
            top: -100,
            right: -160,
            width: 620,
            height: 620,
            borderRadius: '50%',
            background:
              'radial-gradient(circle, oklch(0.86 0.13 163 / 0.32) 0%, oklch(0.92 0.07 130 / 0.18) 40%, transparent 70%)',
            filter: 'blur(8px)',
            animation: 'auth-drift1 12s ease-in-out infinite',
            pointerEvents: 'none',
            zIndex: 0,
          }}
        />
        <div
          aria-hidden
          className="auth-halo"
          style={{
            position: 'fixed',
            bottom: -180,
            left: -120,
            width: 460,
            height: 460,
            borderRadius: '50%',
            background: 'radial-gradient(circle, oklch(0.88 0.09 130 / 0.4) 0%, transparent 70%)',
            filter: 'blur(6px)',
            animation: 'auth-drift2 14s ease-in-out infinite',
            pointerEvents: 'none',
            zIndex: 0,
          }}
        />

        {/* Sparkle stars */}
        {SPARKLES.map((s, i) => (
          <svg
            key={`star-${i}`}
            aria-hidden
            className="auth-star"
            viewBox="0 0 24 24"
            width={s.size}
            height={s.size}
            style={{
              position: 'fixed',
              top: s.top,
              left: s.left,
              pointerEvents: 'none',
              zIndex: 0,
              animation: s.animation,
              filter: `drop-shadow(0 0 6px ${s.color})`,
            }}
          >
            <path d={STAR_PATH} fill={s.color} />
          </svg>
        ))}

        {/* Bubbles */}
        {BUBBLES.map((b, i) => (
          <div
            key={`bubble-${i}`}
            aria-hidden
            className="auth-bubble"
            style={{
              position: 'fixed',
              top: b.top,
              left: b.left,
              width: b.size,
              height: b.size,
              borderRadius: '50%',
              background:
                'radial-gradient(circle at 35% 35%, oklch(1 0 0 / 0.9), oklch(0.92 0.08 163 / 0.45) 60%, transparent)',
              boxShadow: '0 4px 10px -4px oklch(0.6 0.1 163 / 0.3)',
              pointerEvents: 'none',
              zIndex: 0,
              animation: b.animation,
            }}
          />
        ))}

        {/* Top bar */}
        <header
          style={{
            position: 'fixed',
            top: 0,
            left: 0,
            right: 0,
            zIndex: 50,
            display: 'flex',
            alignItems: 'center',
            padding: '22px 40px',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
            <div
              style={{
                width: 30,
                height: 30,
                borderRadius: 9,
                background: 'oklch(0.596 0.145 163.225)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                boxShadow: '0 4px 12px -3px oklch(0.596 0.145 163.225 / 0.5)',
                flexShrink: 0,
              }}
            >
              <span
                style={{ color: '#fff', fontWeight: 800, fontSize: 13, letterSpacing: '-0.02em' }}
              >
                M
              </span>
            </div>
            <span
              className="auth-heading"
              style={{
                fontWeight: 600,
                fontSize: 16,
                letterSpacing: '-0.02em',
                color: 'oklch(0.145 0 0)',
              }}
            >
              Moldy
            </span>
          </div>
        </header>

        {/* Main */}
        <main
          style={{
            flex: 1,
            display: 'flex',
            alignItems: 'stretch',
            padding: 0,
            position: 'relative',
            zIndex: 1,
            gap: 0,
            minHeight: '100vh',
            maxWidth: 1280,
            width: '100%',
            margin: '0 auto',
          }}
        >
          {/* Hero — visible on large screens only */}
          <div
            className="hidden lg:flex"
            style={{
              flex: 1,
              flexDirection: 'column',
              justifyContent: 'center',
              padding: '120px 0 64px 80px',
            }}
          >
            <span
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                width: 'fit-content',
                fontSize: 12,
                fontWeight: 600,
                color: 'oklch(0.596 0.145 163.225)',
                background: 'oklch(0.96 0.06 163)',
                border: '1px solid oklch(0.9 0.07 163)',
                padding: '5px 11px',
                borderRadius: 999,
                marginBottom: 18,
              }}
            >
              <span
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: '50%',
                  background: 'oklch(0.596 0.145 163.225)',
                  flexShrink: 0,
                }}
              />
              AI Agent Builder
            </span>

            <h2
              className="auth-heading"
              style={{
                fontSize: 42,
                fontWeight: 700,
                letterSpacing: '-0.03em',
                lineHeight: 1.1,
                margin: '0 0 16px',
                color: 'oklch(0.145 0 0)',
              }}
            >
              {isLogin ? (
                <>
                  다시 만나서
                  <br />
                  반가워요 👋
                </>
              ) : (
                <>
                  첫 에이전트,
                  <br />
                  같이 만들어볼까요?
                </>
              )}
            </h2>

            <p
              className="auth-muted"
              style={{
                fontSize: 15.5,
                color: 'oklch(0.556 0 0)',
                lineHeight: 1.6,
                maxWidth: 400,
                margin: 0,
              }}
            >
              {isLogin
                ? '작업하던 에이전트를 이어서 빌드하거나, 새 아이디어를 시작해보세요.'
                : '가입 즉시 사용 가능한 템플릿과 도구가 기다리고 있어요.'}
            </p>

            <div style={{ display: 'grid', gap: 10, marginTop: 24 }}>
              {FEATURES.map((text) => (
                <div
                  key={text}
                  className="auth-feature"
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 10,
                    fontSize: 14,
                    color: 'oklch(0.3 0.02 163)',
                  }}
                >
                  <CheckCircle2Icon
                    size={16}
                    style={{ color: 'oklch(0.596 0.145 163.225)', flexShrink: 0 }}
                  />
                  {text}
                </div>
              ))}
            </div>

            <div
              className="auth-muted"
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                marginTop: 28,
                color: 'oklch(0.556 0 0)',
                fontSize: 13,
              }}
            >
              <div style={{ display: 'flex' }}>
                {AVATAR_COLORS.map((bg, i) => (
                  <span
                    key={`avatar-${i}`}
                    aria-hidden
                    style={{
                      width: 22,
                      height: 22,
                      borderRadius: '50%',
                      background: bg,
                      border: '2px solid white',
                      marginLeft: i === 0 ? 0 : -8,
                      display: 'inline-block',
                    }}
                  />
                ))}
              </div>
              <span>
                {t.rich('auth.hero.socialProof', {
                  strong: (chunks) => (
                    <strong
                      className="auth-strong"
                      style={{ color: 'oklch(0.145 0 0)', fontWeight: 600 }}
                    >
                      {chunks}
                    </strong>
                  ),
                })}
              </span>
            </div>
          </div>

          {/* Card column */}
          <div
            style={{
              width: 540,
              flexShrink: 0,
              padding: '120px 80px 64px 0',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <div
              style={{ width: '100%', maxWidth: 380, position: 'relative' } satisfies CSSProperties}
            >
              {/* Floating mascot */}
              <div
                aria-hidden
                style={{
                  position: 'absolute',
                  top: -110,
                  right: -14,
                  zIndex: 2,
                  pointerEvents: 'none',
                  animation: 'auth-float-mascot 5s ease-in-out infinite',
                }}
              >
                <div
                  className="auth-mascot-halo"
                  style={{
                    position: 'absolute',
                    inset: '14% 5% -2% 5%',
                    borderRadius: '50%',
                    background:
                      'radial-gradient(circle, oklch(0.85 0.13 163 / 0.5), transparent 65%)',
                    filter: 'blur(18px)',
                  }}
                />
                <Image
                  src="/moldy-mascot.webp"
                  alt=""
                  width={170}
                  height={170}
                  style={{
                    position: 'relative',
                    filter: 'drop-shadow(0 14px 20px oklch(0.4 0.1 163 / 0.25))',
                  }}
                  draggable={false}
                  priority
                />
              </div>

              {/* Card */}
              <div
                className="auth-card"
                style={{
                  background: '#fff',
                  borderRadius: 22,
                  padding: '30px 34px 28px',
                  border: '1px solid oklch(0.93 0.01 163)',
                  boxShadow:
                    '0 24px 60px -24px oklch(0.4 0.1 163 / 0.22), 0 2px 0 oklch(1 0 0 / 0.6) inset',
                  position: 'relative',
                }}
              >
                {/* Tab switcher */}
                <div
                  data-tab={isLogin ? 'login' : 'register'}
                  role="tablist"
                  style={{
                    position: 'relative',
                    display: 'flex',
                    gap: 2,
                    padding: 3,
                    background: 'oklch(0.96 0.012 163)',
                    border: '1px solid oklch(0.92 0.015 163)',
                    borderRadius: 10,
                    marginBottom: 22,
                  }}
                >
                  <span className="auth-tab-indicator" aria-hidden />
                  <Link
                    href="/login"
                    role="tab"
                    aria-selected={isLogin}
                    style={{
                      flex: 1,
                      height: 36,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      textDecoration: 'none',
                      fontSize: 13,
                      fontWeight: 500,
                      letterSpacing: '-0.01em',
                      color: isLogin ? 'oklch(0.145 0 0)' : 'oklch(0.556 0 0)',
                      borderRadius: 7,
                      position: 'relative',
                      zIndex: 1,
                      transition: 'color .15s',
                    }}
                  >
                    로그인
                  </Link>
                  <Link
                    href="/register"
                    role="tab"
                    aria-selected={!isLogin}
                    style={{
                      flex: 1,
                      height: 36,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      textDecoration: 'none',
                      fontSize: 13,
                      fontWeight: 500,
                      letterSpacing: '-0.01em',
                      color: !isLogin ? 'oklch(0.145 0 0)' : 'oklch(0.556 0 0)',
                      borderRadius: 7,
                      position: 'relative',
                      zIndex: 1,
                      transition: 'color .15s',
                    }}
                  >
                    회원가입
                  </Link>
                </div>

                {children}
              </div>
            </div>
          </div>
        </main>

        <footer
          className="auth-muted"
          style={{
            position: 'relative',
            zIndex: 1,
            padding: '0 80px 24px',
            fontSize: 12,
            color: 'oklch(0.556 0 0)',
          }}
        >
          © 2026 Moldy contributors
        </footer>
      </div>
    </>
  )
}
