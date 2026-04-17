'use client'

import { useState, useCallback } from 'react'
import type { Components } from 'react-markdown'
import Markdown, { defaultUrlTransform } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { CopyIcon, CheckIcon, ImageOffIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'

import 'katex/dist/katex.min.css'
import './markdown-styles.css'

/** Allow sandbox: and file: URLs that LLMs prepend, then delegate to default. */
function urlTransform(url: string): string {
  const cleaned = url.replace(/^(sandbox|file):/, '')
  return defaultUrlTransform(cleaned)
}

function CodeBlock({ language, code }: { language: string; code: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // clipboard API may not be available
    }
  }, [code])

  return (
    <div className="code-block-wrapper">
      <div className="code-block-header">
        <span>{language || 'code'}</span>
        <button
          type="button"
          onClick={handleCopy}
          className="flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-accent transition-colors"
        >
          {copied ? (
            <>
              <CheckIcon className="size-3 text-emerald-500" />
              <span className="text-emerald-500">copied</span>
            </>
          ) : (
            <>
              <CopyIcon className="size-3" />
              <span>copy</span>
            </>
          )}
        </button>
      </div>
      <SyntaxHighlighter
        language={language || 'text'}
        style={oneDark}
        customStyle={{
          margin: 0,
          borderRadius: 0,
          fontSize: '0.75rem',
          lineHeight: 1.5,
        }}
      >
        {code}
      </SyntaxHighlighter>
    </div>
  )
}

export function ChatImage({ src, alt }: { src: string; alt: string }) {
  const [open, setOpen] = useState(false)
  const [error, setError] = useState(false)
  const [loaded, setLoaded] = useState(false)

  const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8001'
  const resolvedSrc = src.startsWith('/api/') ? `${API_BASE}${src}` : src

  if (error) {
    return (
      <div className="chat-image-error">
        <ImageOffIcon className="size-5" />
        <span>Image failed to load</span>
      </div>
    )
  }

  return (
    <>
      <span className="relative inline-block my-2">
        {!loaded && <span className="block w-48 h-32 rounded-lg bg-muted animate-pulse" />}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={resolvedSrc}
          alt={alt}
          className={cn('chat-image', !loaded && 'absolute inset-0 opacity-0')}
          loading="lazy"
          onLoad={() => setLoaded(true)}
          onError={() => setError(true)}
          onClick={() => setOpen(true)}
        />
      </span>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-4xl p-2" showCloseButton>
          <DialogTitle className="sr-only">{alt || 'Image preview'}</DialogTitle>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={resolvedSrc}
            alt={alt}
            className="w-full h-auto max-h-[80vh] object-contain rounded-lg"
          />
        </DialogContent>
      </Dialog>
    </>
  )
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
    const match = codeClassName?.match(/language-(\w+)/)
    if (match) {
      const code = String(children).replace(/\n$/, '')
      return <CodeBlock language={match[1]} code={code} />
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
      className="text-primary underline underline-offset-2 hover:text-primary/80"
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

interface MarkdownContentProps {
  content: string
  className?: string
}

export function MarkdownContent({ content, className }: MarkdownContentProps) {
  return (
    <div className={cn('prose-chat', className)}>
      <Markdown
        components={markdownComponents}
        urlTransform={urlTransform}
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
      >
        {content}
      </Markdown>
    </div>
  )
}
