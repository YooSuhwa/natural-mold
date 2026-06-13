import '@testing-library/jest-dom/vitest'
import { setupServer } from 'msw/node'
import { vi } from 'vitest'

import { handlers } from './mocks/handlers'

// Default next/navigation mock for every jsdom test. Tests that need to
// assert on the router (named ``push``/``replace`` spies) or pin a
// specific ``usePathname`` / ``useParams`` value override this with their
// own ``vi.mock('next/navigation', ...)`` — per-file mocks beat the
// setup-file mock so the override is transparent.
//
// Without this default, any component that calls ``useRouter()`` in a
// jsdom test throws "invariant expected app router to be mounted".
vi.mock('next/navigation', () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    prefetch: vi.fn(),
    back: vi.fn(),
  }),
  usePathname: () => '/',
  useParams: () => ({}),
  useSearchParams: () => new URLSearchParams(),
}))

class ResizeObserverPolyfill {
  observe() {}
  unobserve() {}
  disconnect() {}
}
class IntersectionObserverPolyfill {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return []
  }
}
globalThis.ResizeObserver ??= ResizeObserverPolyfill as unknown as typeof ResizeObserver
globalThis.IntersectionObserver ??=
  IntersectionObserverPolyfill as unknown as typeof IntersectionObserver

function createMemoryStorage(): Storage {
  const store = new Map<string, string>()
  return {
    get length() {
      return store.size
    },
    clear: () => {
      store.clear()
    },
    getItem: (key: string) => store.get(key) ?? null,
    key: (index: number) => Array.from(store.keys())[index] ?? null,
    removeItem: (key: string) => {
      store.delete(key)
    },
    setItem: (key: string, value: string) => {
      store.set(key, value)
    },
  }
}

function hasStorageApi(value: unknown): value is Storage {
  return (
    typeof value === 'object' &&
    value !== null &&
    'getItem' in value &&
    'setItem' in value &&
    'removeItem' in value &&
    'clear' in value
  )
}

function ensureWindowStorage(key: 'localStorage' | 'sessionStorage'): void {
  if (hasStorageApi(window[key])) return
  Object.defineProperty(window, key, {
    configurable: true,
    value: createMemoryStorage(),
  })
}

if (typeof window !== 'undefined') {
  ensureWindowStorage('localStorage')
  ensureWindowStorage('sessionStorage')
  if (!window.matchMedia) {
    window.matchMedia = (query: string) =>
      ({
        matches: false,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }) as MediaQueryList
  }
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = () => {}
  }
  if (!Element.prototype.scrollTo) {
    Element.prototype.scrollTo = (() => {}) as Element['scrollTo']
  }
  if (!HTMLElement.prototype.hasPointerCapture) {
    HTMLElement.prototype.hasPointerCapture = () => false
  }
  if (!HTMLElement.prototype.releasePointerCapture) {
    HTMLElement.prototype.releasePointerCapture = () => {}
  }
}

export const server = setupServer(...handlers)

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())
