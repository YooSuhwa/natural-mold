'use client'

import { useEffect, useState } from 'react'

import { skillsApi } from '@/lib/api/skills'

import { isTextFile } from './skill-detail-file-utils'

export function useSkillFileRemoteCache({
  skillId,
  selectedPath,
  onLoadError,
}: {
  readonly skillId: string
  readonly selectedPath: string | null
  readonly onLoadError: (message: string) => void
}) {
  const [remoteCache, setRemoteCache] = useState<Map<string, string>>(new Map())

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
  }, [skillId, selectedPath, remoteCache, onLoadError])

  return { remoteCache, setRemoteCache }
}
