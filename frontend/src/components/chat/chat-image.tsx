'use client'

import { useState } from 'react'
import { ImageOffIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { API_BASE } from '@/lib/api/client'
import { useApiObjectUrl } from '@/lib/hooks/use-api-object-url'
import { registerOrgScopeReset } from '@/lib/org-scope/org-scope-reset'
import { cn } from '@/lib/utils'
import { DialogShell } from '@/components/shared/dialog-shell'

const loadedImageSources = new Set<string>()
registerOrgScopeReset(() => loadedImageSources.clear())
const CONVERSATION_IMAGE_FILE_RE =
  /\/api\/conversations\/[^/]+\/files\/.+\.(?:png|jpe?g|webp)(?:[?#]|$)/i

export function getChatImagePreviewSrc(resolvedSrc: string): string {
  if (!CONVERSATION_IMAGE_FILE_RE.test(resolvedSrc)) return resolvedSrc
  if (resolvedSrc.includes('variant=preview')) return resolvedSrc
  return `${resolvedSrc}${resolvedSrc.includes('?') ? '&' : '?'}variant=preview`
}

export function ChatImage({ src, alt }: { src: string; alt: string }) {
  const t = useTranslations('chat.markdown')
  const [open, setOpen] = useState(false)

  const resolvedSrc = src.startsWith('/api/') ? `${API_BASE}${src}` : src
  const previewSrc = getChatImagePreviewSrc(resolvedSrc)
  const [previewErrorSrc, setPreviewErrorSrc] = useState<string | null>(null)
  const displaySrc = previewErrorSrc === previewSrc ? resolvedSrc : previewSrc
  const [loadedSrc, setLoadedSrc] = useState<string | null>(() =>
    loadedImageSources.has(displaySrc) ? displaySrc : null,
  )
  const [errorSrc, setErrorSrc] = useState<string | null>(null)
  // API images load through apiFetchBinary + object URLs (plan 13.10);
  // external and data: URLs stay direct.
  const isApiImage = resolvedSrc.startsWith(`${API_BASE}/`)
  const displayBinary = useApiObjectUrl(isApiImage ? displaySrc : null)
  const originalBinary = useApiObjectUrl(isApiImage && open ? resolvedSrc : null)
  const displayImageSrc = isApiImage ? (displayBinary.objectUrl ?? undefined) : displaySrc
  const originalImageSrc = isApiImage ? (originalBinary.objectUrl ?? undefined) : resolvedSrc
  if (isApiImage && displayBinary.status === 'error' && errorSrc !== displaySrc) {
    if (displaySrc !== resolvedSrc) {
      if (previewErrorSrc !== previewSrc) setPreviewErrorSrc(previewSrc)
    } else {
      setErrorSrc(displaySrc)
    }
  }
  const loaded = loadedImageSources.has(displaySrc) || loadedSrc === displaySrc
  const error = errorSrc === displaySrc

  if (error) {
    return (
      <div className="chat-image-error">
        <ImageOffIcon className="size-5" />
        <span>{t('imageLoadFailed')}</span>
      </div>
    )
  }

  return (
    <>
      <span className="relative inline-block my-2">
        {!loaded && <span className="block w-48 h-32 rounded-lg bg-muted animate-pulse" />}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={displayImageSrc}
          alt={alt}
          className={cn('chat-image', !loaded && 'absolute inset-0 opacity-0')}
          loading="lazy"
          onLoad={() => {
            loadedImageSources.add(displaySrc)
            setLoadedSrc(displaySrc)
          }}
          onError={() => {
            if (displaySrc !== resolvedSrc) {
              setPreviewErrorSrc(previewSrc)
              return
            }
            setErrorSrc(displaySrc)
          }}
          onClick={() => setOpen(true)}
        />
      </span>

      <DialogShell
        open={open}
        onOpenChange={setOpen}
        size="xl"
        height="auto"
        className="!h-[calc(100vh-2rem)] !max-h-[calc(100vh-2rem)] !w-[calc(100vw-2rem)] !max-w-[calc(100vw-2rem)] lg:!w-[min(calc(100vw-2rem),1200px)]"
      >
        <DialogShell.Header srOnly title={alt || t('imagePreview')} />
        <DialogShell.Body className="flex min-h-0 items-center justify-center !space-y-0 !overflow-hidden !px-3 !py-3">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={originalImageSrc}
            alt={alt}
            className="block h-auto max-h-full w-auto max-w-full object-contain rounded-lg"
          />
        </DialogShell.Body>
      </DialogShell>
    </>
  )
}
