'use client'

import { lazy, Suspense, useState, useCallback, useMemo } from 'react'
import type { Components } from 'react-markdown'
import Markdown, { defaultUrlTransform } from 'react-markdown'
import rehypeKatex from 'rehype-katex'
import { CHAT_FINAL_REMARK_PLUGINS } from '@/components/chat/markdown-plugins'

// 모듈 레벨 상수 — 매 렌더에서 새 배열 만드는 것 회피.
const REHYPE_PLUGINS = [rehypeKatex]
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { CopyIcon, CheckIcon, ImageOffIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import { API_BASE } from '@/lib/api/client'
import { DialogShell } from '@/components/shared/dialog-shell'

import 'katex/dist/katex.min.css'
import './markdown-styles.css'

// mermaid는 무거우니 동적 import — 메인 번들 보호
const MermaidDiagram = lazy(() =>
  import('./mermaid-diagram').then((m) => ({ default: m.MermaidDiagram })),
)

const loadedImageSources = new Set<string>()
const CONVERSATION_IMAGE_FILE_RE =
  /\/api\/conversations\/[^/]+\/files\/.+\.(?:png|jpe?g|webp)(?:[?#]|$)/i

export function getChatImagePreviewSrc(resolvedSrc: string): string {
  if (!CONVERSATION_IMAGE_FILE_RE.test(resolvedSrc)) return resolvedSrc
  if (resolvedSrc.includes('variant=preview')) return resolvedSrc
  return `${resolvedSrc}${resolvedSrc.includes('?') ? '&' : '?'}variant=preview`
}

/** Allow sandbox: and file: URLs that LLMs prepend, then delegate to default. */
function urlTransform(url: string): string {
  const cleaned = url.replace(/^(sandbox|file):/, '')
  return defaultUrlTransform(cleaned)
}

function CodeBlock({ language, code }: { language: string; code: string }) {
  const t = useTranslations('chat.markdown')
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
          aria-label={copied ? t('copied') : t('copy')}
        >
          {copied ? (
            <>
              <CheckIcon className="size-3 text-status-success" />
              <span className="text-status-success">{t('copied')}</span>
            </>
          ) : (
            <>
              <CopyIcon className="size-3" />
              <span>{t('copy')}</span>
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

function PlainCodeBlock({ language, code }: { language: string; code: string }) {
  return (
    <code
      data-language={language || undefined}
      className="block overflow-x-auto whitespace-pre rounded-md bg-card p-3 font-mono text-xs leading-relaxed text-foreground/90"
    >
      {code}
    </code>
  )
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
        // mermaid 다이어그램: 완료 후에만 SVG 렌더
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

export function MarkdownContent({ content, className, isStreaming = false }: MarkdownContentProps) {
  const components = useMemo(() => buildMarkdownComponents({ isStreaming }), [isStreaming])
  return (
    <div className={cn('prose-chat', className)}>
      <Markdown
        components={components}
        urlTransform={urlTransform}
        remarkPlugins={CHAT_FINAL_REMARK_PLUGINS}
        rehypePlugins={REHYPE_PLUGINS}
      >
        {content}
      </Markdown>
    </div>
  )
}
