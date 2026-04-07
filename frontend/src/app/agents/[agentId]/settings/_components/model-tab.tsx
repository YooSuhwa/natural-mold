import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Slider } from '@/components/ui/slider'
import { ModelSelect } from '@/components/model/model-select'

interface ModelTabProps {
  modelId: string
  onModelIdChange: (v: string) => void
  temperature: number
  onTemperatureChange: (v: number) => void
  topP: number
  onTopPChange: (v: number) => void
  maxTokens: number
  onMaxTokensChange: (v: number) => void
  onReset: () => void
}

export function ModelTab({
  modelId,
  onModelIdChange,
  temperature,
  onTemperatureChange,
  topP,
  onTopPChange,
  maxTokens,
  onMaxTokensChange,
  onReset,
}: ModelTabProps) {
  const t = useTranslations('agent.settings')

  return (
    <div className="space-y-6">
      {/* Model Select */}
      <div className="space-y-2">
        <label className="text-sm font-medium">{t('model')}</label>
        <ModelSelect
          value={modelId}
          onValueChange={onModelIdChange}
          className="rounded-lg border"
        />
      </div>

      {/* Model Parameters */}
      <div className="space-y-5 rounded-lg border p-4">
        <label className="text-sm font-medium">{t('modelParams')}</label>

        {/* Temperature */}
        <div className="space-y-3">
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">{t('temperatureLabel')}</span>
            <span className="font-mono text-xs tabular-nums">{temperature.toFixed(1)}</span>
          </div>
          <Slider
            value={[temperature]}
            onValueChange={(val) =>
              onTemperatureChange(Array.isArray(val) ? val[0] : (val as number))
            }
            min={0}
            max={2}
            step={0.1}
          />
          <div className="flex justify-between text-[10px] text-muted-foreground">
            <span>{t('temperature.accurate')}</span>
            <span>{t('temperature.creative')}</span>
          </div>
        </div>

        {/* Top P */}
        <div className="space-y-3">
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">{t('topPLabel')}</span>
            <span className="font-mono text-xs tabular-nums">{topP.toFixed(1)}</span>
          </div>
          <Slider
            value={[topP]}
            onValueChange={(val) => onTopPChange(Array.isArray(val) ? val[0] : (val as number))}
            min={0}
            max={1}
            step={0.1}
          />
        </div>

        {/* Max Tokens */}
        <div className="space-y-2">
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">{t('maxTokensLabel')}</span>
          </div>
          <Input
            type="number"
            min="256"
            max="32768"
            step="256"
            value={maxTokens}
            onChange={(e) => onMaxTokensChange(Number(e.target.value) || 4096)}
          />
        </div>

        <Button
          variant="ghost"
          size="sm"
          className="text-xs text-muted-foreground"
          onClick={onReset}
        >
          {t('resetToDefault')}
        </Button>
      </div>
    </div>
  )
}
