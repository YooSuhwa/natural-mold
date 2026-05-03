'use client'

import { lazy, Suspense, useState, useCallback, useMemo } from 'react'
import type { Components } from 'react-markdown'
import Markdown, { defaultUrlTransform } from 'react-markdown'
import remarkBreaks from 'remark-breaks'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { CopyIcon, CheckIcon, ImageOffIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { DialogShell } from '@/components/shared/dialog-shell'

import 'katex/dist/katex.min.css'
import './markdown-styles.css'

// mermaid는 무거우니 동적 import — 메인 번들 보호
const MermaidDiagram = lazy(() =>
  import('./mermaid-diagram').then((m) => ({ default: m.MermaidDiagram })),
)

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
              <CheckIcon className="size-3 text-status-success" />
              <span className="text-status-success">copied</span>
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

      <DialogShell open={open} onOpenChange={setOpen} size="xl" height="auto">
        <DialogShell.Header srOnly title={alt || 'Image preview'} />
        <DialogShell.Body className="p-2">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={resolvedSrc}
            alt={alt}
            className="w-full h-auto max-h-[80vh] object-contain rounded-lg"
          />
        </DialogShell.Body>
      </DialogShell>
    </>
  )
}

function buildMarkdownComponents({ isStreaming }: { isStreaming: boolean }): Components {
  return {
    p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
    strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
    em: ({ children }) => <em className="italic">{children}</em>,
    ul: ({ children }) => (
      <ul className="mb-2 ml-4 list-disc space-y-0.5 last:mb-0">{children}</ul>
    ),
    ol: ({ children }) => (
      <ol className="mb-2 ml-4 list-decimal space-y-0.5 last:mb-0">{children}</ol>
    ),
    code: ({ children, className: codeClassName }) => {
      const match = codeClassName?.match(/language-(\w+)/)
      if (match) {
        const language = match[1]
        const code = String(children).replace(/\n$/, '')
        // mermaid 다이어그램: 스트리밍 중에는 raw code(불완전 파싱 방지), 완료 후 SVG 렌더
        if (language === 'mermaid' && !isStreaming) {
          return (
            <Suspense fallback={<CodeBlock language="mermaid" code={code} />}>
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

interface MarkdownContentProps {
  content: string
  className?: string
  isStreaming?: boolean
}

export function MarkdownContent({
  content,
  className,
  isStreaming = false,
}: MarkdownContentProps) {
  const components = useMemo(() => buildMarkdownComponents({ isStreaming }), [isStreaming])
  return (
    <div className={cn('prose-chat', className)}>
      <Markdown
        components={components}
        urlTransform={urlTransform}
        // remarkBreaks: 단일 newline을 <br>로 변환. LLM 응답이 종종 단일 줄바꿈으로
        // 줄을 나눠 쓰는데 GitHub Markdown은 빈 줄(double newline)만 단락 분기로
        // 인식해서 시각적으로 합쳐져 보이는 문제를 해소.
        remarkPlugins={[remarkGfm, remarkMath, remarkBreaks]}
        rehypePlugins={[rehypeKatex]}
      >
        {content}
      </Markdown>
    </div>
  )
}
