import { useEffect, useRef, useState } from 'react'
import type { ArtifactPreviewProvider, ArtifactPreviewProps } from '../preview-registry'
import { DocumentPreviewShell } from './document-preview-shell'
import { useArtifactArrayBuffer } from './use-artifact-binary'

export function DocxPreview({ artifact }: ArtifactPreviewProps) {
  const binary = useArtifactArrayBuffer(artifact)
  const bodyRef = useRef<HTMLDivElement | null>(null)
  const styleRef = useRef<HTMLDivElement | null>(null)
  const [renderError, setRenderError] = useState<string | null>(null)
  const [isRendering, setIsRendering] = useState(false)

  useEffect(() => {
    if (!binary.data || !bodyRef.current || !styleRef.current) return
    let cancelled = false
    void (async () => {
      try {
        setIsRendering(true)
        setRenderError(null)
        const docx = await import('docx-preview')
        if (cancelled || !bodyRef.current || !styleRef.current) return
        bodyRef.current.replaceChildren()
        styleRef.current.replaceChildren()
        await docx.renderAsync(binary.data, bodyRef.current, styleRef.current, {
          className: 'moldy-docx',
          inWrapper: true,
          ignoreFonts: false,
          ignoreWidth: true,
          ignoreHeight: true,
          useBase64URL: true,
        })
      } catch (error) {
        if (!cancelled) setRenderError(error instanceof Error ? error.message : String(error))
      } finally {
        if (!cancelled) setIsRendering(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [binary.data])

  const error = binary.error instanceof Error ? binary.error.message : renderError

  return (
    <DocumentPreviewShell
      artifact={artifact}
      title={artifact.display_name}
      isLoading={binary.isLoading}
      error={error}
    >
      <div className="moldy-docx-wrapper max-h-[620px] overflow-auto bg-background p-3">
        {isRendering ? (
          <div className="px-4 py-8 text-center text-sm text-muted-foreground">
            {artifact.display_name}
          </div>
        ) : null}
        <div ref={styleRef} />
        <div ref={bodyRef} />
      </div>
    </DocumentPreviewShell>
  )
}

export const DocxPreviewProvider: ArtifactPreviewProvider = {
  id: 'docx',
  priority: 88,
  requiresText: false,
  extensions: ['docx'],
  mimeTypes: ['application/vnd.openxmlformats-officedocument.wordprocessingml.document'],
  render: (props) => <DocxPreview {...props} />,
}
