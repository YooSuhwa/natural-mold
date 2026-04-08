import { useTranslations } from 'next-intl'
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
        <AgentAvatar imageUrl={imageUrl} name={name} size="lg" />
        <Button
          variant="outline"
          size="sm"
          onClick={() => generateImage()}
          disabled={isPending}
        >
          {isPending ? (
            <><Loader2Icon className="size-4 animate-spin" />{t('image.generating')}</>
          ) : imageUrl ? (
            <><RefreshCwIcon className="size-4" />{t('image.regenerate')}</>
          ) : (
            <><ImagePlusIcon className="size-4" />{t('image.generate')}</>
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
