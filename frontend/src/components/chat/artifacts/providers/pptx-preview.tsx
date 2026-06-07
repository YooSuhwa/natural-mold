import { ChevronLeftIcon, ChevronRightIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import type { ArtifactPreviewProvider, ArtifactPreviewProps } from '../preview-registry'
import { DocumentPreviewShell } from './document-preview-shell'
import { useArtifactArrayBuffer } from './use-artifact-binary'

export function PptxPreview({ artifact }: ArtifactPreviewProps) {
  const t = useTranslations('chat.rightRail.artifacts.documentPreview')
  const binary = useArtifactArrayBuffer(artifact)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const viewerRef = useRef<import('pptxviewjs').PPTXViewer | null>(null)
  const [slideIndex, setSlideIndex] = useState(0)
  const [slideCount, setSlideCount] = useState(0)
  const [renderError, setRenderError] = useState<string | null>(null)
  const [isRendering, setIsRendering] = useState(false)

  useEffect(() => {
    if (!binary.data || !canvasRef.current) return
    let cancelled = false
    let viewer: import('pptxviewjs').PPTXViewer | null = null
    void (async () => {
      try {
        setIsRendering(true)
        setRenderError(null)
        const { PPTXViewer } = await import('pptxviewjs')
        if (cancelled || !canvasRef.current) return
        viewer = new PPTXViewer({
          canvas: canvasRef.current,
          autoExposeGlobals: true,
          autoChartRerenderDelayMs: 0,
          autoRenderFirstSlide: false,
          enableThumbnails: false,
        })
        await viewer.loadFile(binary.data)
        if (cancelled) {
          viewer.destroy()
          return
        }
        viewerRef.current = viewer
        const total = Math.max(1, viewer.getSlideCount())
        setSlideCount(total)
        setSlideIndex(0)
        await viewer.renderSlide(0, canvasRef.current)
      } catch (error) {
        if (!cancelled) setRenderError(error instanceof Error ? error.message : String(error))
      } finally {
        if (!cancelled) setIsRendering(false)
      }
    })()
    return () => {
      cancelled = true
      viewer?.destroy()
      if (viewerRef.current === viewer) viewerRef.current = null
    }
  }, [binary.data])

  async function goToSlide(nextIndex: number) {
    if (!canvasRef.current || !viewerRef.current) return
    const safeIndex = Math.max(0, Math.min((slideCount || 1) - 1, nextIndex))
    setIsRendering(true)
    try {
      await viewerRef.current.goToSlide(safeIndex, canvasRef.current)
      setSlideIndex(safeIndex)
    } catch (error) {
      setRenderError(error instanceof Error ? error.message : String(error))
    } finally {
      setIsRendering(false)
    }
  }

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
            onClick={() => void goToSlide(slideIndex - 1)}
            disabled={slideIndex <= 0 || isRendering}
            aria-label={t('previousPage')}
          >
            <ChevronLeftIcon className="size-3.5" />
          </Button>
          <span className="min-w-16 text-center text-xs text-muted-foreground">
            {t('page', { current: slideIndex + 1, total: slideCount || 1 })}
          </span>
          <Button
            variant="ghost"
            size="icon-xs"
            onClick={() => void goToSlide(slideIndex + 1)}
            disabled={slideIndex >= (slideCount || 1) - 1 || isRendering}
            aria-label={t('nextPage')}
          >
            <ChevronRightIcon className="size-3.5" />
          </Button>
        </>
      }
    >
      <div className="bg-background p-3">
        <div className="mx-auto h-52 w-full overflow-hidden border border-border bg-white sm:h-56">
          <canvas
            ref={canvasRef}
            data-testid="pptx-preview-canvas"
            width={1280}
            height={720}
            className="block h-full w-full"
          />
        </div>
      </div>
    </DocumentPreviewShell>
  )
}

export const PptxPreviewProvider: ArtifactPreviewProvider = {
  id: 'pptx',
  priority: 86,
  requiresText: false,
  extensions: ['pptx'],
  mimeTypes: ['application/vnd.openxmlformats-officedocument.presentationml.presentation'],
  render: (props) => <PptxPreview {...props} />,
}
