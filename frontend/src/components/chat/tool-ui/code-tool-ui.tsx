'use client'

import { useState } from 'react'
import { makeAssistantToolUI } from '@assistant-ui/react'
import { useTranslations } from 'next-intl'
import {
  CopyIcon,
  CheckIcon,
  FileIcon,
  FileEditIcon,
  FilePlusIcon,
  type LucideIcon,
} from 'lucide-react'
import { CollapsiblePill, pillStatusFromAssistantUi, type PillStatus } from './collapsible-pill'

// ──────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────

interface ReadFileArgs {
  file_path?: string
  path?: string
}

interface WriteFileArgs {
  file_path?: string
  path?: string
  content?: string
}

interface EditFileArgs {
  file_path?: string
  path?: string
  old_string?: string
  new_string?: string
}

// ──────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────

function extractFilename(path?: string): string {
  if (!path) return 'file'
  const segments = path.split('/')
  return segments[segments.length - 1] ?? 'file'
}

function guessLanguage(filename: string): string {
  const ext = filename.split('.').pop()?.toLowerCase()
  const map: Record<string, string> = {
    ts: 'typescript',
    tsx: 'typescript',
    js: 'javascript',
    jsx: 'javascript',
    py: 'python',
    rs: 'rust',
    go: 'go',
    java: 'java',
    json: 'json',
    yaml: 'yaml',
    yml: 'yaml',
    md: 'markdown',
    css: 'css',
    html: 'html',
    sql: 'sql',
    sh: 'bash',
    toml: 'toml',
  }
  return ext ? (map[ext] ?? ext) : 'text'
}

export function shouldFileToolDefaultExpand({
  label,
  status,
  hasPreview,
}: {
  label: string
  status: PillStatus
  hasPreview: boolean
}): boolean {
  if (label === 'Read') return false
  return status !== 'loading' && hasPreview
}

// ──────────────────────────────────────────────
// CodeBlock — 코드 미리보기 (Shiki 없이 기본 스타일)
// ──────────────────────────────────────────────

function CodeBlock({
  code,
  filename,
  maxLines = 20,
}: {
  code: string
  filename: string
  maxLines?: number
}) {
  const t = useTranslations('chat.markdown')
  const [copied, setCopied] = useState(false)
  const lines = code.split('\n')
  const truncated = lines.length > maxLines
  const visibleLines = truncated ? lines.slice(0, maxLines) : lines
  const lang = guessLanguage(filename)

  const handleCopy = async () => {
    await navigator.clipboard.writeText(code)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="overflow-hidden rounded-lg border border-border/40 bg-zinc-950 text-[11px]">
      {/* File header */}
      <div className="flex items-center justify-between border-b border-white/10 bg-zinc-900 px-3 py-1.5">
        <span className="font-mono text-zinc-400">{filename}</span>
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-zinc-500">{lang}</span>
          <button
            type="button"
            onClick={handleCopy}
            className="text-zinc-500 transition-colors hover:text-zinc-300"
            aria-label={copied ? t('copied') : t('copy')}
            title={copied ? t('copied') : t('copy')}
          >
            {copied ? (
              <CheckIcon className="size-3 text-status-success" />
            ) : (
              <CopyIcon className="size-3" />
            )}
          </button>
        </div>
      </div>
      {/* Code */}
      <div className="overflow-x-auto p-3">
        <pre className="font-mono leading-relaxed text-zinc-300">
          {visibleLines.map((line, i) => (
            <div key={i} className="flex">
              <span className="mr-4 inline-block w-8 select-none text-right text-zinc-600">
                {i + 1}
              </span>
              <span className="flex-1">{line || ' '}</span>
            </div>
          ))}
          {truncated && (
            <div className="mt-1 text-center text-zinc-500">
              … {t('moreLines', { count: lines.length - maxLines })}
            </div>
          )}
        </pre>
      </div>
    </div>
  )
}

// ──────────────────────────────────────────────
// DiffBlock — edit_file 전용 diff 표시
// ──────────────────────────────────────────────

function DiffBlock({
  oldStr,
  newStr,
  filename,
}: {
  oldStr: string
  newStr: string
  filename: string
}) {
  return (
    <div className="overflow-hidden rounded-lg border border-border/40 bg-zinc-950 text-[11px]">
      <div className="border-b border-white/10 bg-zinc-900 px-3 py-1.5">
        <span className="font-mono text-zinc-400">{filename}</span>
      </div>
      <div className="overflow-x-auto p-3 font-mono leading-relaxed">
        {oldStr.split('\n').map((line, i) => (
          <div key={`old-${i}`} className="bg-status-danger/15 text-status-danger">
            <span className="mr-2 select-none opacity-60">-</span>
            {line || ' '}
          </div>
        ))}
        {newStr.split('\n').map((line, i) => (
          <div key={`new-${i}`} className="bg-status-success/15 text-status-success">
            <span className="mr-2 select-none opacity-60">+</span>
            {line || ' '}
          </div>
        ))}
      </div>
    </div>
  )
}

// ──────────────────────────────────────────────
// FileToolPill — Read/Write/Edit 공통 래퍼. leadingIcon으로 file 종류
// (Read/Write/Edit)을 시각적으로도 구분 (textual label과 함께 빠른 스캔성 확보).
// ──────────────────────────────────────────────

function FileToolPill({
  icon,
  label,
  filePath,
  status,
  children,
}: {
  icon: LucideIcon
  label: string
  filePath?: string
  status: PillStatus
  children?: React.ReactNode
}) {
  return (
    <CollapsiblePill
      kind="tool"
      leadingIcon={icon}
      status={status}
      title={label}
      meta={extractFilename(filePath)}
      defaultExpanded={shouldFileToolDefaultExpand({
        label,
        status,
        hasPreview: Boolean(children),
      })}
    >
      {children}
    </CollapsiblePill>
  )
}

// ──────────────────────────────────────────────
// ReadFileToolUI
// ──────────────────────────────────────────────

export const ReadFileToolUI = makeAssistantToolUI<ReadFileArgs, unknown>({
  toolName: 'read_file',
  render: ({ args, result, status }) => (
    <ReadFileToolView args={args} result={result} statusType={status.type} />
  ),
})

function ReadFileToolView({
  args,
  result,
  statusType,
}: {
  args: ReadFileArgs
  result: unknown
  statusType: string
}) {
  const t = useTranslations('chat.toolCall.file')
  const filePath = args?.file_path ?? args?.path
  const filename = extractFilename(filePath)
  const content = typeof result === 'string' ? result : null

  return (
    <FileToolPill
      icon={FileIcon}
      label={t('read')}
      filePath={filePath}
      status={pillStatusFromAssistantUi(statusType)}
    >
      {content && <CodeBlock code={content} filename={filename} />}
    </FileToolPill>
  )
}

// ──────────────────────────────────────────────
// WriteFileToolUI
// ──────────────────────────────────────────────

export const WriteFileToolUI = makeAssistantToolUI<WriteFileArgs, unknown>({
  toolName: 'write_file',
  render: ({ args, status }) => <WriteFileToolView args={args} statusType={status.type} />,
})

function WriteFileToolView({ args, statusType }: { args: WriteFileArgs; statusType: string }) {
  const t = useTranslations('chat.toolCall.file')
  const filePath = args?.file_path ?? args?.path
  const filename = extractFilename(filePath)

  return (
    <FileToolPill
      icon={FilePlusIcon}
      label={t('write')}
      filePath={filePath}
      status={pillStatusFromAssistantUi(statusType)}
    >
      {args?.content && <CodeBlock code={args.content} filename={filename} />}
    </FileToolPill>
  )
}

// ──────────────────────────────────────────────
// EditFileToolUI
// ──────────────────────────────────────────────

export const EditFileToolUI = makeAssistantToolUI<EditFileArgs, unknown>({
  toolName: 'edit_file',
  render: ({ args, status }) => <EditFileToolView args={args} statusType={status.type} />,
})

function EditFileToolView({ args, statusType }: { args: EditFileArgs; statusType: string }) {
  const t = useTranslations('chat.toolCall.file')
  const filePath = args?.file_path ?? args?.path
  const filename = extractFilename(filePath)
  const hasEdit = args?.old_string && args?.new_string

  return (
    <FileToolPill
      icon={FileEditIcon}
      label={t('edit')}
      filePath={filePath}
      status={pillStatusFromAssistantUi(statusType)}
    >
      {hasEdit && (
        <DiffBlock oldStr={args.old_string!} newStr={args.new_string!} filename={filename} />
      )}
    </FileToolPill>
  )
}
