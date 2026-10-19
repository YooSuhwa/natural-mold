'use client'

import { useState } from 'react'

/** Keyset paging state: a stack of cursors so "previous" can walk back. */
export function useCursorPager() {
  const [stack, setStack] = useState<(string | null)[]>([null])
  const cursor = stack[stack.length - 1] ?? null
  return {
    cursor,
    pageIndex: stack.length - 1,
    canGoBack: stack.length > 1,
    next: (nextCursor: string | null) => {
      if (nextCursor) setStack((current) => [...current, nextCursor])
    },
    back: () => setStack((current) => (current.length > 1 ? current.slice(0, -1) : current)),
    reset: () => setStack([null]),
  }
}
