/* eslint-disable @next/next/no-img-element */
import { ChevronLeftIcon, ChevronRightIcon, ZoomInIcon, ZoomOutIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import type { ArtifactPreviewProvider, ArtifactPreviewProps } from '../preview-registry'
import { DocumentPreviewShell } from './document-preview-shell'
import { useArtifactArrayBuffer } from './use-artifact-binary'

type RhwpModule = typeof import('@rhwp/core')

let rhwpInitPromise: Promise<RhwpModule> | null = null

function ensureMeasureTextWidth() {
  const target = globalThis as typeof globalThis & {
    measureTextWidth?: (font: string, text: string) => number
  }
  if (target.measureTextWidth) return
  const canvas = document.createElement('canvas')
  const context = canvas.getContext('2d')
  target.measureTextWidth = (font, text) => {
    if (!context) return text.length * 10
    context.font = font
    return context.measureText(text).width
  }
}

async function loadRhwp() {
  if (!rhwpInitPromise) {
    rhwpInitPromise = import('@rhwp/core').then(async (module) => {
      ensureMeasureTextWidth()
      await module.default({ module_or_path: '/vendor/rhwp/rhwp_bg.wasm' })
      return module
    })
  }
  return rhwpInitPromise
}

function zoomClass(zoom: number): string {
  if (zoom <= 75) return 'w-3/4'
  if (zoom >= 150) return 'w-[150%]'
  if (zoom >= 125) return 'w-[125%]'
  return 'w-full'
}

export function HwpPreview({ artifact }: ArtifactPreviewProps) {
  const t = useTranslations('chat.rightRail.artifacts.documentPreview')
  const binary = useArtifactArrayBuffer(artifact)
  const [pageIndex, setPageIndex] = useState(0)
  const [pageCount, setPageCount] = useState(0)
  const [pageUrl, setPageUrl] = useState<string | null>(null)
  const [zoom, setZoom] = useState(100)
  const [renderError, setRenderError] = useState<string | null>(null)

  useEffect(() => {
    if (!binary.data) return
    let cancelled = false
    let objectUrl: string | null = null
    let documentInstance: InstanceType<RhwpModule['HwpDocument']> | null = null

    void (async () => {
      try {
        setRenderError(null)
        const rhwp = await loadRhwp()
        if (cancelled) return
        documentInstance = new rhwp.HwpDocument(new Uint8Array(binary.data))
        documentInstance.setDpi(96)
        const total = Math.max(1, documentInstance.pageCount())
        const safePageIndex = Math.min(pageIndex, total - 1)
        const svg = documentInstance.renderPageSvg(safePageIndex)
        objectUrl = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }))
        if (!cancelled) {
          setPageCount(total)
          setPageIndex(safePageIndex)
          setPageUrl(objectUrl)
        }
      } catch (error) {
        if (!cancelled) setRenderError(error instanceof Error ? error.message : String(error))
      } finally {
        documentInstance?.free()
      }
    })()

    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [binary.data, pageIndex])

  const error = binary.error instanceof Error ? binary.error.message : renderError

  return (
    <DocumentPreviewShell
      artifact={artifact}
      title={artifact.display_name}
      isLoading={binary.isLoading}
      error={error}
      toolbar={
        <>
          <Button
            variant="ghost"
            size="icon-xs"
            onClick={() => setPageIndex((current) => Math.max(0, current - 1))}
            disabled={pageIndex <= 0}
            aria-label={t('previousPage')}
          >
            <ChevronLeftIcon className="size-3.5" />
          </Button>
          <span className="min-w-16 text-center text-xs text-muted-foreground">
            {t('page', { current: pageIndex + 1, total: pageCount || 1 })}
          </span>
          <Button
            variant="ghost"
            size="icon-xs"
            onClick={() => setPageIndex((current) => Math.min((pageCount || 1) - 1, current + 1))}
            disabled={pageIndex >= (pageCount || 1) - 1}
            aria-label={t('nextPage')}
          >
            <ChevronRightIcon className="size-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon-xs"
            onClick={() => setZoom((current) => Math.max(75, current - 25))}
            aria-label={t('zoomOut')}
          >
            <ZoomOutIcon className="size-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon-xs"
            onClick={() => setZoom((current) => Math.min(150, current + 25))}
            aria-label={t('zoomIn')}
          >
            <ZoomInIcon className="size-3.5" />
          </Button>
        </>
      }
    >
      <div className="max-h-[620px] overflow-auto bg-background p-3">
        {pageUrl ? (
          <img
            src={pageUrl}
            alt={artifact.display_name}
            className={cn(
              'mx-auto h-auto max-w-none border border-border bg-white',
              zoomClass(zoom),
            )}
          />
        ) : (
          <div className="px-4 py-8 text-center text-sm text-muted-foreground">{t('loading')}</div>
        )}
      </div>
    </DocumentPreviewShell>
  )
}

export const HwpPreviewProvider: ArtifactPreviewProvider = {
  id: 'hwp-hwpx',
  priority: 89,
  requiresText: false,
  extensions: ['hwp', 'hwpx'],
  mimeTypes: ['application/x-hwp', 'application/x-hwpx', 'application/hwp+zip'],
  render: (props) => <HwpPreview {...props} />,
}
