'use client'

import { lazy, Suspense } from 'react'
import { languageForExtension } from './code-language'

// 챗 마크다운과 동일한 하이라이터를 재사용한다 — heavy 라이브러리 규칙에 따라
// lazy 로드하고, 로딩/비지원 언어/대용량 파일은 기존 plain <pre>로 폴백한다.
const MarkdownCodeHighlighter = lazy(() => import('../markdown-code-highlighter'))

const HIGHLIGHT_MAX_LINES = 1500
const HIGHLIGHT_MAX_CHARS = 200_000

function PlainArtifactCode({ text }: { text: string }) {
  return (
    <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-foreground">
      {text}
    </pre>
  )
}

export function ArtifactCodeBlock({
  text,
  extension,
}: {
  text: string
  extension: string | null | undefined
}) {
  const language = languageForExtension(extension)
  const tooLarge =
    text.length > HIGHLIGHT_MAX_CHARS || text.split('\n').length > HIGHLIGHT_MAX_LINES
  if (!language || tooLarge || !text) {
    return <PlainArtifactCode text={text} />
  }
  return (
    <Suspense fallback={<PlainArtifactCode text={text} />}>
      <MarkdownCodeHighlighter language={language} code={text} />
    </Suspense>
  )
}
