'use client'

import {
  CopyIcon,
  DownloadIcon,
  ExternalLinkIcon,
  FileTextIcon,
  PencilIcon,
  SaveIcon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'
import type { DeepAgentFile } from '@/lib/chat/langgraph-runtime/deepagents-state'

type FilePreviewKind = 'markdown' | 'code' | 'plainText' | 'file'

export interface DeepAgentsStateFileActions {
  readonly onCopyFile?: (file: DeepAgentFile) => void | Promise<void>
  readonly onDownloadFile?: (file: DeepAgentFile) => void
  readonly onOpenPreview?: (file: DeepAgentFile) => void
  readonly onEditFile?: (file: DeepAgentFile) => void
  readonly onSaveFile?: (file: DeepAgentFile) => void
}

interface FilesBodyProps extends DeepAgentsStateFileActions {
  readonly files: readonly DeepAgentFile[]
}

const FILE_ACTION_CLASS =
  'inline-flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40'

function extension(path: string): string {
  const fileName = path.split('/').at(-1) ?? path
  const parts = fileName.split('.')
  return parts.length > 1 ? (parts.at(-1)?.toLowerCase() ?? '') : ''
}

function filePreviewKind(file: DeepAgentFile): FilePreviewKind {
  const mimeType = file.mimeType?.toLowerCase() ?? ''
  const ext = extension(file.path)
  if (file.artifactKind === 'markdown' || mimeType.includes('markdown') || ext === 'md') {
    return 'markdown'
  }
  if (
    file.artifactKind === 'code' ||
    mimeType.includes('x-python') ||
    mimeType.includes('javascript') ||
    ['py', 'ts', 'tsx', 'js', 'jsx', 'json', 'yaml', 'yml', 'css', 'html'].includes(ext)
  ) {
    return 'code'
  }
  if (mimeType.startsWith('text/') || ['txt', 'log'].includes(ext)) return 'plainText'
  return 'file'
}

async function copyFile(
  file: DeepAgentFile,
  onCopyFile?: (file: DeepAgentFile) => void | Promise<void>,
) {
  if (onCopyFile) {
    await onCopyFile(file)
    return
  }
  if (file.content && navigator.clipboard) await navigator.clipboard.writeText(file.content)
}

function FileRow({
  file,
  onCopyFile,
  onDownloadFile,
  onOpenPreview,
  onEditFile,
  onSaveFile,
}: { readonly file: DeepAgentFile } & DeepAgentsStateFileActions) {
  const t = useTranslations('chat.deepAgentsState.files')
  const previewable = Boolean(file.previewUrl || file.content)
  const kind = filePreviewKind(file)
  return (
    <li className="flex min-w-0 items-center gap-2 py-1">
      <FileTextIcon className="size-3.5 shrink-0 text-muted-foreground" />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-xs font-medium text-foreground">{file.name}</span>
        <span className="block truncate text-xs text-muted-foreground">{file.path}</span>
      </span>
      <span className="shrink-0 rounded-md border border-border px-1.5 py-0.5 moldy-ui-micro text-muted-foreground">
        {t(`kinds.${kind}`)}
      </span>
      <span className="flex shrink-0 items-center gap-1">
        {previewable ? (
          <button
            type="button"
            className={FILE_ACTION_CLASS}
            aria-label={t('actions.openPreview', { name: file.name })}
            onClick={() => onOpenPreview?.(file)}
          >
            <ExternalLinkIcon className="size-3.5" />
          </button>
        ) : null}
        {file.content ? (
          <button
            type="button"
            className={FILE_ACTION_CLASS}
            aria-label={t('actions.copy', { name: file.name })}
            onClick={() => {
              void copyFile(file, onCopyFile)
            }}
          >
            <CopyIcon className="size-3.5" />
          </button>
        ) : null}
        {file.downloadUrl ? (
          <a
            className={FILE_ACTION_CLASS}
            href={file.downloadUrl}
            rel="noopener noreferrer"
            aria-label={t('actions.download', { name: file.name })}
            onClick={() => onDownloadFile?.(file)}
          >
            <DownloadIcon className="size-3.5" />
          </a>
        ) : null}
        <button
          type="button"
          className={FILE_ACTION_CLASS}
          aria-label={t('actions.edit', { name: file.name })}
          disabled
          onClick={() => onEditFile?.(file)}
        >
          <PencilIcon className="size-3.5" />
        </button>
        <button
          type="button"
          className={FILE_ACTION_CLASS}
          aria-label={t('actions.save', { name: file.name })}
          disabled
          onClick={() => onSaveFile?.(file)}
        >
          <SaveIcon className="size-3.5" />
        </button>
      </span>
    </li>
  )
}

export function DeepAgentsStateFileList({ files, ...actions }: FilesBodyProps) {
  return (
    <ol className="space-y-1">
      {files.map((file) => (
        <FileRow key={file.id} file={file} {...actions} />
      ))}
    </ol>
  )
}
