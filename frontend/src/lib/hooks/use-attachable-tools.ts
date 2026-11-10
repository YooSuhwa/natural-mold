'use client'

import { useMemo } from 'react'

import { useTools } from '@/lib/hooks/use-tools'
import { attachable } from '@/lib/tools/attachable'

/** `useTools()` for agent pickers: without tools still waiting for registration approval. */
export function useAttachableTools(keep?: ReadonlySet<string> | readonly { id: string }[]) {
  const { data: all } = useTools()
  const data = useMemo(() => {
    if (!all) return undefined
    const ids =
      keep === undefined || keep instanceof Set
        ? keep
        : new Set((keep as readonly { id: string }[]).map((row) => row.id))
    return attachable(all, ids)
  }, [all, keep])
  return { data }
}
