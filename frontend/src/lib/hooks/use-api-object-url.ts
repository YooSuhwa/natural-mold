'use client'

import { useEffect, useState } from 'react'

import { apiFetchBinary } from '@/lib/api/client'

export type ApiObjectUrlState =
  | { readonly status: 'idle' | 'loading'; readonly objectUrl: null }
  | { readonly status: 'ready'; readonly objectUrl: string }
  | { readonly status: 'error'; readonly objectUrl: null }

interface LoadedEntry {
  readonly url: string
  readonly state: ApiObjectUrlState
}

/**
 * Load an API resource through `apiFetchBinary` (refresh retry, structured
 * errors, org-scoped abort — plan 13.10) and expose it as an object URL. The
 * object URL is revoked when `url` changes or the component unmounts (an
 * organization switch unmounts the whole org subtree), and the request is
 * aborted with it.
 */
export function useApiObjectUrl(url: string | null): ApiObjectUrlState {
  const [entry, setEntry] = useState<LoadedEntry | null>(null)

  useEffect(() => {
    if (!url) return
    const controller = new AbortController()
    let objectUrl: string | null = null
    apiFetchBinary(url, controller.signal)
      .then((blob) => {
        if (controller.signal.aborted) return
        objectUrl = URL.createObjectURL(blob)
        setEntry({ url, state: { status: 'ready', objectUrl } })
      })
      .catch(() => {
        if (controller.signal.aborted) return
        setEntry({ url, state: { status: 'error', objectUrl: null } })
      })
    return () => {
      controller.abort()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [url])

  if (!url) return { status: 'idle', objectUrl: null }
  if (entry?.url !== url) return { status: 'loading', objectUrl: null }
  return entry.state
}

/** Download an API resource as `filename` via a short-lived object URL. */
export async function downloadApiFile(url: string, filename: string): Promise<void> {
  const blob = await apiFetchBinary(url)
  const objectUrl = URL.createObjectURL(blob)
  try {
    const anchor = document.createElement('a')
    anchor.href = objectUrl
    anchor.download = filename
    anchor.rel = 'noopener'
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
  } finally {
    // Revoke after the click has been dispatched to the download manager.
    setTimeout(() => URL.revokeObjectURL(objectUrl), 0)
  }
}
