import { NextResponse, type NextRequest } from 'next/server'

const REFRESH_COOKIE = 'moldy_rt'
const PUBLIC_ROUTES = new Set<string>(['/login', '/register'])
const PUBLIC_PREFIXES = ['/shared/'] as const

function isPublic(pathname: string): boolean {
  if (PUBLIC_ROUTES.has(pathname)) return true
  return PUBLIC_PREFIXES.some((p) => pathname.startsWith(p))
}

/**
 * Cookie-based gate (no JWT validation — that's the API's job).
 *
 * - any protected route without cookie → bounce to `/login?callbackUrl=…`
 *
 * NOTE: We intentionally do NOT redirect `/login`→`/` based on cookie presence.
 * The cookie may be expired, and the proxy can't verify JWT validity at the edge.
 * Doing so causes an infinite redirect loop when the refresh token is expired:
 *   / → 401 → /login, proxy sees cookie → /, / → 401 → …
 * The auth layout handles the logged-in redirect client-side via useSession().
 */
export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl
  const hasRefresh = Boolean(request.cookies.get(REFRESH_COOKIE))

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
  matcher: [
    '/((?!api|_next/static|_next/image|favicon.ico|fonts|.*\\.(?:svg|png|jpg|jpeg|webp|ico)).*)',
  ],
}
