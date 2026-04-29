'use client'

import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { ImagePlusIcon, RefreshCwIcon, Loader2Icon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { AgentAvatar } from '@/components/agent/agent-avatar'
import { useGenerateAgentImage } from '@/lib/hooks/use-agents'

interface SettingsPanelProps {
  agentId: string
  imageUrl: string | null
  name: string
}

export function SettingsPanel({ agentId, imageUrl, name }: SettingsPanelProps) {
  const t = useTranslations('agent.settings')
  const tc = useTranslations('common')
  const { mutate: generateImage, isPending } = useGenerateAgentImage(agentId)

  function handleGenerate() {
    generateImage(undefined, {
      onSuccess: () => toast.success(t('image.success')),
      onError: () => toast.error(t('image.failed')),
    })
  }

  function handleRemove() {
    // 이미지 제거 API는 다음 PR — placeholder
    toast.info(tc('comingSoon.default'))
  }

  return (
    <div className="flex flex-col items-center gap-4 rounded-xl border bg-background p-6">
      <div className="relative">
        <AgentAvatar imageUrl={imageUrl} name={name} size="lg" />
        {isPending && (
          <div className="absolute inset-0 flex items-center justify-center rounded-full bg-background/60">
            <Loader2Icon className="size-6 animate-spin text-primary" />
          </div>
        )}
      </div>

      {isPending && (
        <div className="w-48 space-y-1.5">
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
            <div className="h-full animate-[progress_30s_ease-out_forwards] rounded-full bg-primary" />
          </div>
          <p className="text-center text-xs text-muted-foreground">{t('image.generating')}</p>
        </div>
      )}

      <div className="flex flex-col items-center gap-2">
        <Button variant="outline" size="sm" onClick={handleGenerate} disabled={isPending}>
          {imageUrl ? (
            <>
              <RefreshCwIcon className="size-4" />
              {t('image.regenerate')}
            </>
          ) : (
            <>
              <ImagePlusIcon className="size-4" />
              {t('image.generate')}
            </>
          )}
        </Button>
        {imageUrl && (
          <Button variant="ghost" size="sm" onClick={handleRemove} disabled={isPending}>
            {t('imageRemove')}
          </Button>
        )}
      </div>
    </div>
  )
}
