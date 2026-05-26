'use client'

import { useQuery } from '@tanstack/react-query'
import { CheckCircle2Icon } from 'lucide-react'
import Image from 'next/image'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useEffect, type ReactNode } from 'react'

import { SESSION_QUERY_KEY } from '@/lib/auth/session'
import type { User } from '@/lib/types/user'

const FEATURES = [
  '대화로 만드는 노코드 에이전트',
  'LangGraph 분기 · 시간여행 지원',
  '암호화된 API 키 · 안전한 도구 호출',
]

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8001'

export default function AuthLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname()
  const router = useRouter()
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

  // Already authenticated — suppress form flash while redirect is pending.
  if (user) return null

  return (
    <>
      <style>{`
        @keyframes auth-float-mascot {
          0%, 100% { transform: rotate(6deg) translateY(0); }
          50% { transform: rotate(6deg) translateY(-6px); }
        }
        @keyframes auth-drift1 { 0%,100%{transform:translate(0,0)} 50%{transform:translate(-6px,-10px)} }
        @keyframes auth-drift2 { 0%,100%{transform:translate(0,0)} 50%{transform:translate(8px,-6px)} }
        .auth-tab-indicator {
          position: absolute; top: 3px; bottom: 3px; left: 3px;
          width: calc(50% - 3px);
          background: #fff;
          border-radius: 7px;
          box-shadow: 0 1px 2px oklch(0 0 0 / 0.06), 0 1px 1px oklch(0 0 0 / 0.04);
          transition: transform .25s cubic-bezier(.2,.7,.2,1);
        }
        [data-tab="register"] .auth-tab-indicator { transform: translateX(100%); }
      `}</style>

      <div
        style={{
          minHeight: '100vh',
          background:
            'linear-gradient(180deg, oklch(0.985 0.012 100) 0%, oklch(0.97 0.025 163) 100%)',
          position: 'relative',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        {/* Ambient blobs */}
        <div
          aria-hidden
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

        {/* Top bar */}
        <header
          style={{
            position: 'fixed',
            top: 0,
            left: 0,
            right: 0,
            zIndex: 50,
            display: 'flex',
            justifyContent: 'space-between',
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
          <div style={{ fontSize: 13, color: 'oklch(0.556 0 0)' }}>
            {isLogin ? (
              <>
                처음이신가요?{' '}
                <Link
                  href="/register"
                  style={{
                    color: 'oklch(0.596 0.145 163.225)',
                    fontWeight: 600,
                    textDecoration: 'none',
                  }}
                >
                  회원가입 →
                </Link>
              </>
            ) : (
              <>
                이미 계정이 있나요?{' '}
                <Link
                  href="/login"
                  style={{
                    color: 'oklch(0.596 0.145 163.225)',
                    fontWeight: 600,
                    textDecoration: 'none',
                  }}
                >
                  로그인 →
                </Link>
              </>
            )}
          </div>
        </header>

        {/* Main */}
        <main
          style={{
            flex: 1,
            display: 'flex',
            alignItems: 'center',
            padding: '100px 80px 60px',
            position: 'relative',
            zIndex: 1,
            gap: 0,
            minHeight: '100vh',
          }}
        >
          {/* Hero — visible on large screens only */}
          <div
            className="hidden lg:flex"
            style={{ flex: 1, flexDirection: 'column', paddingRight: 60, maxWidth: 560 }}
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

            {/* <div
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
                    key={i}
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
                <strong style={{ color: 'oklch(0.145 0 0)', fontWeight: 600 }}>200+ 팀</strong>이
                Moldy로 에이전트를 만들고 있어요
              </span>
            </div> */}
          </div>

          {/* Card column */}
          <div style={{ width: 400, flexShrink: 0, position: 'relative' }}>
            {/* Floating mascot */}
            <div
              aria-hidden
              style={{
                position: 'absolute',
                top: -110,
                right: -24,
                zIndex: 2,
                pointerEvents: 'none',
                animation: 'auth-float-mascot 5s ease-in-out infinite',
              }}
            >
              <div
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

            {/* Bottom switch link */}
            <p
              style={{
                textAlign: 'center',
                fontSize: 13.5,
                color: 'oklch(0.556 0 0)',
                marginTop: 18,
                marginBottom: 0,
              }}
            >
              {isLogin ? (
                <>
                  아직 계정이 없나요?{' '}
                  <Link
                    href="/register"
                    style={{
                      color: 'oklch(0.596 0.145 163.225)',
                      fontWeight: 600,
                      textDecoration: 'none',
                    }}
                  >
                    30초 만에 가입하기
                  </Link>
                </>
              ) : (
                <>
                  이미 계정이 있나요?{' '}
                  <Link
                    href="/login"
                    style={{
                      color: 'oklch(0.596 0.145 163.225)',
                      fontWeight: 600,
                      textDecoration: 'none',
                    }}
                  >
                    로그인 →
                  </Link>
                </>
              )}
            </p>
          </div>
        </main>

        <footer
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
