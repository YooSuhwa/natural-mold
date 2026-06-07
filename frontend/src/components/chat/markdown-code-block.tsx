'use client'

import { lazy, Suspense, useCallback, useState } from 'react'
import { CheckIcon, CopyIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

const MarkdownCodeHighlighter = lazy(() => import('./markdown-code-highlighter'))

export function CodeBlock({ language, code }: { language: string; code: string }) {
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
      <Suspense fallback={<PlainCodeBlock language={language} code={code} />}>
        <MarkdownCodeHighlighter language={language} code={code} />
      </Suspense>
    </div>
  )
}

export function PlainCodeBlock({ language, code }: { language: string; code: string }) {
  return (
    <code
      data-language={language || undefined}
      className="block overflow-x-auto whitespace-pre rounded-md bg-card p-3 font-mono text-xs leading-relaxed text-foreground/90"
    >
      {code}
    </code>
  )
}
