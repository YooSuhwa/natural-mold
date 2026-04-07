import { useTranslations } from 'next-intl'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'

interface BasicInfoTabProps {
  name: string
  onNameChange: (v: string) => void
  description: string
  onDescriptionChange: (v: string) => void
  systemPrompt: string
  onSystemPromptChange: (v: string) => void
}

export function BasicInfoTab({
  name,
  onNameChange,
  description,
  onDescriptionChange,
  systemPrompt,
  onSystemPromptChange,
}: BasicInfoTabProps) {
  const t = useTranslations('agent.settings')

  return (
    <div className="space-y-6">
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
