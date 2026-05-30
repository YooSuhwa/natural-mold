'use client'

import { useEffect, useId, useState } from 'react'
import mermaid from 'mermaid'
import { useTheme } from 'next-themes'
import { useTranslations } from 'next-intl'

type MermaidTheme = 'default' | 'dark'

let currentTheme: MermaidTheme | null = null

function ensureInitialized(theme: MermaidTheme) {
  if (currentTheme === theme) return
  mermaid.initialize({
    startOnLoad: false,
    theme,
    securityLevel: 'strict',
    fontFamily: 'inherit',
  })
  currentTheme = theme
}

interface MermaidDiagramProps {
  code: string
}

export function MermaidDiagram({ code }: MermaidDiagramProps) {
  const t = useTranslations('chat.mermaid')
  const rawId = useId()
  const id = rawId.replace(/[^a-zA-Z0-9-]/g, '-')
  const { resolvedTheme } = useTheme()
  const [svg, setSvg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  // resolvedTheme이 hydration 직전엔 undefined → 'default'로 폴백 (SSR과 일치).
  // mounted 후 'dark'로 바뀌면 effect deps 변경으로 자연스럽게 재렌더.
  const mermaidTheme: MermaidTheme = resolvedTheme === 'dark' ? 'dark' : 'default'

  useEffect(() => {
    ensureInitialized(mermaidTheme)
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
        setError(e instanceof Error ? e.message : t('renderFailed'))
      })
    return () => {
      cancelled = true
    }
  }, [id, code, mermaidTheme, t])

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
