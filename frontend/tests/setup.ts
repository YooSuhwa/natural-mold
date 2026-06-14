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
class MemoryStorage implements Storage {
  private readonly items = new Map<string, string>()

  get length(): number {
    return this.items.size
  }

  clear(): void {
    this.items.clear()
  }

  getItem(key: string): string | null {
    return this.items.get(key) ?? null
  }

  key(index: number): string | null {
    return Array.from(this.items.keys())[index] ?? null
  }

  removeItem(key: string): void {
    this.items.delete(key)
  }

  setItem(key: string, value: string): void {
    this.items.set(key, value)
  }
}
globalThis.ResizeObserver ??= ResizeObserverPolyfill as unknown as typeof ResizeObserver
globalThis.IntersectionObserver ??=
  IntersectionObserverPolyfill as unknown as typeof IntersectionObserver

if (typeof window !== 'undefined') {
  const storage = window.localStorage
  if (
    typeof storage?.getItem !== 'function' ||
    typeof storage.setItem !== 'function' ||
    typeof storage.clear !== 'function'
  ) {
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      value: new MemoryStorage(),
    })
  }
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
  if (!HTMLElement.prototype.setPointerCapture) {
    HTMLElement.prototype.setPointerCapture = () => {}
  }
  if (!HTMLElement.prototype.releasePointerCapture) {
    HTMLElement.prototype.releasePointerCapture = () => {}
  }
}

export const server = setupServer(...handlers)

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())
