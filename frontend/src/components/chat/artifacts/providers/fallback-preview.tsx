import { DownloadIcon, FileIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import { resolveImageUrl } from '@/lib/utils'
import type { ArtifactPreviewProvider, ArtifactPreviewProps } from '../preview-registry'

function FallbackPreview({ artifact }: ArtifactPreviewProps) {
  const t = useTranslations('chat.rightRail.artifacts')
  return (
    <div className="moldy-muted-panel flex flex-col items-center gap-3 px-4 py-8 text-center">
      <FileIcon className="size-8 text-muted-foreground" />
      <div>
        <p className="text-sm font-medium text-foreground">{t('fallbackTitle')}</p>
        <p className="mt-1 text-xs text-muted-foreground">{t('fallbackBody')}</p>
      </div>
      <Button
        size="sm"
        render={<a href={resolveImageUrl(artifact.download_url) ?? artifact.download_url} />}
      >
        <DownloadIcon className="size-4" />
        {t('download')}
      </Button>
    </div>
  )
}

export const FallbackPreviewProvider: ArtifactPreviewProvider = {
  id: 'fallback',
  priority: 0,
  requiresText: false,
  match: () => true,
  render: (props) => <FallbackPreview {...props} />,
}
