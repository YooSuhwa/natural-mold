'use client'

import { useEffect, useState } from 'react'

export function readViewportWidth(): number {
  return typeof window === 'undefined' ? Number.POSITIVE_INFINITY : window.innerWidth
}

export function useViewportWidth(): number {
  const [viewportWidth, setViewportWidth] = useState(readViewportWidth)

  useEffect(() => {
    if (typeof window === 'undefined') return

    function handleResize() {
      setViewportWidth(readViewportWidth())
    }

    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  return viewportWidth
}
