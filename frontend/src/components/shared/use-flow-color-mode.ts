'use client'

import { useTheme } from 'next-themes'

/** React Flow's ``colorMode`` from the app theme (next-themes), so canvases follow dark mode. */
export function useFlowColorMode(): 'light' | 'dark' {
  const { resolvedTheme } = useTheme()
  return resolvedTheme === 'dark' ? 'dark' : 'light'
}
