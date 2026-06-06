'use client'

import { lazy, Suspense } from 'react'
import type { Components } from 'react-markdown'
import { ChatImage } from '@/components/chat/chat-image'
import { CodeBlock, PlainCodeBlock } from '@/components/chat/markdown-code-block'

const MermaidDiagram = lazy(() =>
  import('./mermaid-diagram').then((m) => ({ default: m.MermaidDiagram })),
)

export function buildMarkdownComponents({ isStreaming }: { isStreaming: boolean }): Components {
  return {
    p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
    strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
    em: ({ children }) => <em className="italic">{children}</em>,
    ul: ({ children }) => <ul className="mb-2 ml-4 list-disc space-y-0.5 last:mb-0">{children}</ul>,
    ol: ({ children }) => (
      <ol className="mb-2 ml-4 list-decimal space-y-0.5 last:mb-0">{children}</ol>
    ),
    code: ({ children, className: codeClassName }) => {
      const match = codeClassName?.match(/language-(\w+)/)
      if (match) {
        const language = match[1]
        const code = String(children).replace(/\n$/, '')
        if (isStreaming) {
          return <PlainCodeBlock language={language} code={code} />
        }
        if (language === 'mermaid') {
          return (
            <Suspense fallback={<PlainCodeBlock language="mermaid" code={code} />}>
              <MermaidDiagram code={code} />
            </Suspense>
          )
        }
        return <CodeBlock language={language} code={code} />
      }
      return (
        <code className="rounded bg-foreground/10 px-1 py-0.5 text-xs font-mono">{children}</code>
      )
    },
    pre: ({ children }) => <pre className="mb-2 last:mb-0 [&>div]:!mb-0">{children}</pre>,
    blockquote: ({ children }) => (
      <blockquote className="mb-2 border-l-2 border-foreground/20 pl-3 text-muted-foreground last:mb-0">
        {children}
      </blockquote>
    ),
    img: ({ src, alt }) => {
      if (!src || typeof src !== 'string') return null
      return <ChatImage src={src} alt={alt ?? ''} />
    },
    a: ({ href, children }) => (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="text-primary-strong underline underline-offset-2 hover:text-primary-strong/80"
      >
        {children}
      </a>
    ),
    table: ({ children }) => (
      <div className="overflow-x-auto mb-2 last:mb-0">
        <table>{children}</table>
      </div>
    ),
    h1: ({ children }) => <p className="mb-2 text-base font-bold last:mb-0">{children}</p>,
    h2: ({ children }) => <p className="mb-2 text-base font-bold last:mb-0">{children}</p>,
    h3: ({ children }) => <p className="mb-1.5 font-semibold last:mb-0">{children}</p>,
    hr: () => <hr className="my-3 border-foreground/10" />,
  }
}
