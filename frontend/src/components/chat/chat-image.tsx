'use client'

import { useState } from 'react'
import { ImageOffIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { API_BASE } from '@/lib/api/client'
import { cn } from '@/lib/utils'
import { DialogShell } from '@/components/shared/dialog-shell'

const loadedImageSources = new Set<string>()
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
          src={displaySrc}
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
            src={resolvedSrc}
            alt={alt}
            className="block h-auto max-h-full w-auto max-w-full object-contain rounded-lg"
          />
        </DialogShell.Body>
      </DialogShell>
    </>
  )
}
