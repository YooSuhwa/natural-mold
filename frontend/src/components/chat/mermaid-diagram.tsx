'use client'

import { useEffect, useId, useState } from 'react'
import mermaid from 'mermaid'

let initialized = false
function ensureInitialized() {
  if (initialized) return
  mermaid.initialize({
    startOnLoad: false,
    theme: 'default',
    securityLevel: 'loose',
    fontFamily: 'inherit',
  })
  initialized = true
}

interface MermaidDiagramProps {
  code: string
}

export function MermaidDiagram({ code }: MermaidDiagramProps) {
  const rawId = useId()
  const id = rawId.replace(/[^a-zA-Z0-9-]/g, '-')
  const [svg, setSvg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    ensureInitialized()
    let cancelled = false
    // P1-C polish — explicit promise handle so cleanup can also rely on the
    // cancelled flag for any in-flight tail callbacks. mermaid.render returns
    // a Promise; we let it settle but ignore results once cancelled.
    const renderPromise = mermaid.render(`mermaid-${id}`, code)
    renderPromise
      .then(({ svg }) => {
        if (cancelled) return
        setSvg(svg)
        setError(null)
      })
      .catch((e: unknown) => {
        if (cancelled) return
        setError(e instanceof Error ? e.message : 'Mermaid render failed')
      })
    return () => {
      cancelled = true
    }
  }, [id, code])

  if (error) {
    return (
      <pre className="overflow-auto rounded-md border border-border/60 bg-card p-3 text-xs">
        <code>{code}</code>
      </pre>
    )
  }

  if (!svg) {
    return (
      <pre className="overflow-auto rounded-md border border-border/60 bg-card p-3 text-xs">
        <code>{code}</code>
      </pre>
    )
  }

  return (
    <div
      className="overflow-auto rounded-md border border-border/60 bg-card p-3 [&_svg]:max-w-full [&_svg]:h-auto"
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  )
}
