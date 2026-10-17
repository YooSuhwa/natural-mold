'use client'

import { useEffect, useState } from 'react'

/** `value` after it stopped changing for `delayMs` (search-as-you-type without a request per key). */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(timer)
  }, [value, delayMs])
  return debounced
}
