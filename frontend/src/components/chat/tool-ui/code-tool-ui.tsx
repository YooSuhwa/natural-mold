'use client'

import { useState } from 'react'
import { makeAssistantToolUI } from '@assistant-ui/react'
import {
  FileIcon,
  FileEditIcon,
  FilePlusIcon,
  ChevronDownIcon,
  CheckCircle2Icon,
  Loader2Icon,
  CopyIcon,
  CheckIcon,
} from 'lucide-react'
import { cn } from '@/lib/utils'

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
          >
            {copied ? (
              <CheckIcon className="size-3 text-emerald-400" />
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
            <div className="mt-1 text-center text-zinc-500">… {lines.length - maxLines}줄 더</div>
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
          <div key={`old-${i}`} className="bg-red-950/40 text-red-300">
            <span className="mr-2 select-none text-red-500/60">-</span>
            {line || ' '}
          </div>
        ))}
        {newStr.split('\n').map((line, i) => (
          <div key={`new-${i}`} className="bg-emerald-950/40 text-emerald-300">
            <span className="mr-2 select-none text-emerald-500/60">+</span>
            {line || ' '}
          </div>
        ))}
      </div>
    </div>
  )
}

// ──────────────────────────────────────────────
// FileToolWrapper — 공통 레이아웃
// ──────────────────────────────────────────────

function FileToolWrapper({
  icon: Icon,
  label,
  filePath,
  isRunning,
  children,
}: {
  icon: typeof FileIcon
  label: string
  filePath?: string
  isRunning: boolean
  children?: React.ReactNode
}) {
  const [expanded, setExpanded] = useState(!isRunning)
  const filename = extractFilename(filePath)

  return (
    <div className="w-full rounded-xl border bg-muted/20 text-xs">
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
        onClick={() => setExpanded(!expanded)}
      >
        {isRunning ? (
          <Loader2Icon className="size-3.5 shrink-0 animate-spin text-primary" />
        ) : (
          <CheckCircle2Icon className="size-3.5 shrink-0 text-emerald-500" />
        )}
        <Icon className="size-3 shrink-0 text-muted-foreground" />
        <span className="truncate font-medium">{label}</span>
        <span className="truncate text-muted-foreground">{filename}</span>
        <span className="ml-auto" />
        {children && (
          <ChevronDownIcon
            className={cn(
              'size-3.5 shrink-0 text-muted-foreground transition-transform duration-200',
              expanded && 'rotate-180',
            )}
          />
        )}
      </button>
      {expanded && children && <div className="px-3 pb-3">{children}</div>}
    </div>
  )
}

// ──────────────────────────────────────────────
// ReadFileToolUI
// ──────────────────────────────────────────────

export const ReadFileToolUI = makeAssistantToolUI<ReadFileArgs, unknown>({
  toolName: 'read_file',
  render: ({ args, result, status }) => {
    const isRunning = status.type === 'running'
    const filePath = args?.file_path ?? args?.path
    const filename = extractFilename(filePath)
    const content = typeof result === 'string' ? result : null

    return (
      <FileToolWrapper icon={FileIcon} label="Read" filePath={filePath} isRunning={isRunning}>
        {content && <CodeBlock code={content} filename={filename} />}
      </FileToolWrapper>
    )
  },
})

// ──────────────────────────────────────────────
// WriteFileToolUI
// ──────────────────────────────────────────────

export const WriteFileToolUI = makeAssistantToolUI<WriteFileArgs, unknown>({
  toolName: 'write_file',
  render: ({ args, status }) => {
    const isRunning = status.type === 'running'
    const filePath = args?.file_path ?? args?.path
    const filename = extractFilename(filePath)

    return (
      <FileToolWrapper icon={FilePlusIcon} label="Write" filePath={filePath} isRunning={isRunning}>
        {args?.content && <CodeBlock code={args.content} filename={filename} />}
      </FileToolWrapper>
    )
  },
})

// ──────────────────────────────────────────────
// EditFileToolUI
// ──────────────────────────────────────────────

export const EditFileToolUI = makeAssistantToolUI<EditFileArgs, unknown>({
  toolName: 'edit_file',
  render: ({ args, status }) => {
    const isRunning = status.type === 'running'
    const filePath = args?.file_path ?? args?.path
    const filename = extractFilename(filePath)
    const hasEdit = args?.old_string && args?.new_string

    return (
      <FileToolWrapper icon={FileEditIcon} label="Edit" filePath={filePath} isRunning={isRunning}>
        {hasEdit && (
          <DiffBlock oldStr={args.old_string!} newStr={args.new_string!} filename={filename} />
        )}
      </FileToolWrapper>
    )
  },
})
