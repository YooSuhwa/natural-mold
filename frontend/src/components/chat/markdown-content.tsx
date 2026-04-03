'use client'

import type { Components } from 'react-markdown'
import Markdown, { defaultUrlTransform } from 'react-markdown'
import { cn } from '@/lib/utils'

/** Allow sandbox: and file: URLs that LLMs prepend, then delegate to default. */
function urlTransform(url: string): string {
  const cleaned = url.replace(/^(sandbox|file):/, '')
  return defaultUrlTransform(cleaned)
}

const markdownComponents: Components = {
  p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  ul: ({ children }) => <ul className="mb-2 ml-4 list-disc space-y-0.5 last:mb-0">{children}</ul>,
  ol: ({ children }) => (
    <ol className="mb-2 ml-4 list-decimal space-y-0.5 last:mb-0">{children}</ol>
  ),
  code: ({ children, className: codeClassName }) => {
    if (codeClassName?.includes('language-')) {
      return (
        <code className="block overflow-x-auto rounded-md bg-foreground/5 p-2.5 text-xs font-mono">
          {children}
        </code>
      )
    }
    return (
      <code className="rounded bg-foreground/10 px-1 py-0.5 text-xs font-mono">{children}</code>
    )
  },
  pre: ({ children }) => <pre className="mb-2 last:mb-0">{children}</pre>,
  blockquote: ({ children }) => (
    <blockquote className="mb-2 border-l-2 border-foreground/20 pl-3 text-muted-foreground last:mb-0">
      {children}
    </blockquote>
  ),
  img: ({ src, alt }) => {
    if (!src || typeof src !== 'string') return null
    const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8001'
    // urlTransform already strips sandbox:/file: prefixes; resolve /api/ paths to backend
    const resolvedSrc = src.startsWith('/api/') ? `${API_BASE}${src}` : src
    return (
      // eslint-disable-next-line @next/next/no-img-element -- LLM 응답의 동적 외부 URL이므로 next/image 최적화 불가
      <img
        src={resolvedSrc}
        alt={alt ?? ''}
        className="rounded-lg max-w-full my-2"
        loading="lazy"
      />
    )
  },
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-primary underline underline-offset-2 hover:text-primary/80"
    >
      {children}
    </a>
  ),
  h1: ({ children }) => <p className="mb-2 text-base font-bold last:mb-0">{children}</p>,
  h2: ({ children }) => <p className="mb-2 text-base font-bold last:mb-0">{children}</p>,
  h3: ({ children }) => <p className="mb-1.5 font-semibold last:mb-0">{children}</p>,
  hr: () => <hr className="my-3 border-foreground/10" />,
}

interface MarkdownContentProps {
  content: string
  className?: string
}

export function MarkdownContent({ content, className }: MarkdownContentProps) {
  return (
    <div className={cn('prose-chat', className)}>
      <Markdown components={markdownComponents} urlTransform={urlTransform}>
        {content}
      </Markdown>
    </div>
  )
}
