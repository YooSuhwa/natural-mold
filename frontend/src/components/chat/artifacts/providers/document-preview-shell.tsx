import { DownloadIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import type { ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { resolveImageUrl } from '@/lib/utils'
import type { ArtifactSummary } from '@/lib/types'

interface DocumentPreviewShellProps {
  artifact: ArtifactSummary
  title: string
  isLoading?: boolean
  error?: string | null
  toolbar?: ReactNode
  children: ReactNode
}

export function DocumentPreviewShell({
  artifact,
  title,
  isLoading = false,
  error = null,
  toolbar,
  children,
}: DocumentPreviewShellProps) {
  const t = useTranslations('chat.rightRail.artifacts.documentPreview')
  const downloadUrl = resolveImageUrl(artifact.download_url) ?? artifact.download_url

  if (isLoading) {
    return (
      <div className="moldy-muted-panel px-4 py-8 text-center text-sm text-muted-foreground">
        {t('loading')}
      </div>
    )
  }

  if (error) {
    return (
      <div className="moldy-muted-panel flex flex-col items-center gap-3 px-4 py-8 text-center">
        <div>
          <p className="text-sm font-medium text-foreground">{t('errorTitle')}</p>
          <p className="mt-1 text-xs text-muted-foreground">{error}</p>
        </div>
        <Button size="sm" render={<a href={downloadUrl} />}>
          <DownloadIcon className="size-4" />
          {t('downloadInstead')}
        </Button>
      </div>
    )
  }

  return (
    <div className="overflow-hidden border border-border bg-card">
      <div className="flex min-h-10 items-center justify-between gap-3 border-b border-border/60 px-3 py-2 text-xs">
        <span className="min-w-0 truncate font-medium text-foreground">{title}</span>
        {toolbar ? <div className="flex shrink-0 items-center gap-1">{toolbar}</div> : null}
      </div>
      {children}
    </div>
  )
}
