'use client'

import { useCallback, useEffect, useState, type SetStateAction } from 'react'

import { skillsApi } from '@/lib/api/skills'

import { isTextFile } from './skill-detail-file-utils'

type RemoteCacheState = {
  readonly scope: string
  readonly values: Map<string, string>
}

const EMPTY_CACHE = new Map<string, string>()

export function useSkillFileRemoteCache({
  skillId,
  cacheKey,
  selectedPath,
  onLoadError,
}: {
  readonly skillId: string
  readonly cacheKey: string | null | undefined
  readonly selectedPath: string | null
  readonly onLoadError: (message: string) => void
}) {
  const cacheScope = `${skillId}:${cacheKey ?? 'pending'}`
  const [cacheState, setCacheState] = useState<RemoteCacheState>({
    scope: cacheScope,
    values: new Map(),
  })
  const remoteCache = cacheState.scope === cacheScope ? cacheState.values : EMPTY_CACHE
  const setRemoteCache = useCallback(
    (update: SetStateAction<Map<string, string>>) => {
      setCacheState((prev) => {
        const base = prev.scope === cacheScope ? prev.values : new Map<string, string>()
        const values = typeof update === 'function' ? update(base) : update
        return { scope: cacheScope, values }
      })
    },
    [cacheScope],
  )

  useEffect(() => {
    if (!selectedPath) return
    if (!isTextFile(selectedPath)) return
    if (remoteCache.has(selectedPath)) return

    let cancelled = false
    fetch(skillsApi.fileUrl(skillId, selectedPath), { credentials: 'include' })
      .then((response) =>
        response.ok ? response.text() : Promise.reject(new Error(`HTTP ${response.status}`)),
      )
      .then((body) => {
        if (cancelled) return
        setRemoteCache((prev) => {
          const next = new Map(prev)
          next.set(selectedPath, body)
          return next
        })
      })
      .catch((error) => {
        if (cancelled) return
        onLoadError(error instanceof Error ? error.message : 'unknown')
      })

    return () => {
      cancelled = true
    }
  }, [skillId, selectedPath, remoteCache, setRemoteCache, onLoadError])

  return { remoteCache, setRemoteCache }
}
