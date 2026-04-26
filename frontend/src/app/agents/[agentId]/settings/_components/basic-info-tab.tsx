import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { Loader2Icon, ImagePlusIcon, RefreshCwIcon } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Button } from '@/components/ui/button'
import { AgentAvatar } from '@/components/agent/agent-avatar'
import { useGenerateAgentImage } from '@/lib/hooks/use-agents'

interface BasicInfoTabProps {
  name: string
  onNameChange: (v: string) => void
  description: string
  onDescriptionChange: (v: string) => void
  systemPrompt: string
  onSystemPromptChange: (v: string) => void
  agentId: string
  imageUrl: string | null
}

export function BasicInfoTab({
  name,
  onNameChange,
  description,
  onDescriptionChange,
  systemPrompt,
  onSystemPromptChange,
  agentId,
  imageUrl,
}: BasicInfoTabProps) {
  const t = useTranslations('agent.settings')
  const { mutate: generateImage, isPending } = useGenerateAgentImage(agentId)

  return (
    <div className="space-y-6">
      <div className="flex flex-col items-center gap-3 pb-6 border-b">
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
            <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
              <div className="h-full rounded-full bg-primary animate-[progress_30s_ease-out_forwards]" />
            </div>
            <p className="text-xs text-muted-foreground text-center">{t('image.generating')}</p>
          </div>
        )}
        <Button
          variant="outline"
          size="sm"
          onClick={() =>
            generateImage(undefined, {
              onSuccess: () => toast.success(t('image.success')),
              onError: () => toast.error(t('image.failed')),
            })
          }
          disabled={isPending}
        >
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
      </div>

      <div className="space-y-2">
        <label className="text-sm font-medium">{t('name')}</label>
        <Input value={name} onChange={(e) => onNameChange(e.target.value)} />
      </div>

      <div className="space-y-2">
        <label className="text-sm font-medium">{t('description')}</label>
        <Input
          value={description}
          onChange={(e) => onDescriptionChange(e.target.value)}
          placeholder={t('descriptionPlaceholder')}
        />
      </div>

      <div className="space-y-2">
        <label className="text-sm font-medium">{t('systemPrompt')}</label>
        <Textarea
          value={systemPrompt}
          onChange={(e) => onSystemPromptChange(e.target.value)}
          rows={8}
          className="font-mono text-xs"
        />
      </div>
    </div>
  )
}
