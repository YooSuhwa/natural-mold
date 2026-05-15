import { NextResponse, type NextRequest } from 'next/server'

const REFRESH_COOKIE = 'moldy_rt'
const PUBLIC_ROUTES = new Set<string>(['/login', '/register'])
const PUBLIC_PREFIXES = ['/shared/'] as const

function isPublic(pathname: string): boolean {
  if (PUBLIC_ROUTES.has(pathname)) return true
  return PUBLIC_PREFIXES.some((p) => pathname.startsWith(p))
}

function isAuthRoute(pathname: string): boolean {
  return pathname === '/login' || pathname === '/register'
}

/**
 * Cookie-based gate (no JWT validation — that's the API's job).
 *
 * - `/login`, `/register` with cookie present → bounce to `/`
 * - any protected route without cookie → bounce to `/login?callbackUrl=…`
 */
export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl
  const hasRefresh = Boolean(request.cookies.get(REFRESH_COOKIE))

  if (isAuthRoute(pathname) && hasRefresh) {
    const url = request.nextUrl.clone()
    url.pathname = '/'
    url.search = ''
    return NextResponse.redirect(url)
  }

  if (!isPublic(pathname) && !hasRefresh) {
    const url = request.nextUrl.clone()
    url.pathname = '/login'
    url.search = ''
    url.searchParams.set('callbackUrl', pathname + (search || ''))
    return NextResponse.redirect(url)
  }

  return NextResponse.next()
}

export const config = {
  matcher: ['/((?!api|_next/static|_next/image|favicon.ico|fonts|.*\\.(?:svg|png|jpg|jpeg|webp|ico)).*)'],
}
