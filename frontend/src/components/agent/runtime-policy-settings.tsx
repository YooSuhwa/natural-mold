'use client'

import { useTranslations } from 'next-intl'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { hasPositiveContextWindow } from '@/lib/agents/runtime-policy-validation'
import type { RuntimePolicyV1 } from '@/lib/types/runtime-policy'

export type RuntimePolicySettingsSurface = 'existing-agent' | 'new-agent'

export interface RuntimePolicySettingsProps {
  readonly value: RuntimePolicyV1 | null
  readonly onValueChange: (value: RuntimePolicyV1 | null) => void
  readonly contextWindow: number | null
  readonly surface: RuntimePolicySettingsSurface
  readonly collapsible?: boolean
}

const CUSTOM_RUNTIME_POLICY: RuntimePolicyV1 = {
  version: 1,
  filesystem: { mode: 'artifact_write' },
  todo: { enabled: true },
  summarization: { mode: 'auto' },
}

function createCustomRuntimePolicy(): RuntimePolicyV1 {
  return {
    version: CUSTOM_RUNTIME_POLICY.version,
    filesystem: { ...CUSTOM_RUNTIME_POLICY.filesystem },
    todo: { ...CUSTOM_RUNTIME_POLICY.todo },
    summarization: { ...CUSTOM_RUNTIME_POLICY.summarization },
  }
}

function updateFilesystem(
  policy: RuntimePolicyV1,
  mode: RuntimePolicyV1['filesystem']['mode'],
): RuntimePolicyV1 {
  return { ...policy, filesystem: { mode } }
}

function updateTodo(policy: RuntimePolicyV1, enabled: boolean): RuntimePolicyV1 {
  return { ...policy, todo: { enabled } }
}

function updateSummarization(
  policy: RuntimePolicyV1,
  mode: RuntimePolicyV1['summarization'],
): RuntimePolicyV1 {
  return { ...policy, summarization: mode }
}

export function RuntimePolicySettings({
  value,
  onValueChange,
  contextWindow,
  surface,
  collapsible = false,
}: RuntimePolicySettingsProps) {
  const t = useTranslations('agent.settings.runtimePolicy')
  const isRecommended = value === null
  const contextWindowAvailable = hasPositiveContextWindow(contextWindow)
  const balancedSelected = value?.summarization.mode === 'preset'
  const invalidBalancedSelection = balancedSelected && !contextWindowAvailable
  const controlDisabled = isRecommended
  const balancedHelpId = 'runtime-policy-balanced-help'

  function selectCustomPolicy() {
    if (!isRecommended) return
    onValueChange(createCustomRuntimePolicy())
  }

  function selectRecommendedPolicy() {
    onValueChange(null)
  }

  function changeFilesystem(mode: RuntimePolicyV1['filesystem']['mode']) {
    if (!value) return
    onValueChange(updateFilesystem(value, mode))
  }

  function changeTodo(enabled: boolean) {
    if (!value) return
    onValueChange(updateTodo(value, enabled))
  }

  function changeSummarization(mode: RuntimePolicyV1['summarization']) {
    if (!value) return
    onValueChange(updateSummarization(value, mode))
  }

  const settings = (
    <section aria-labelledby="runtime-policy-title" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 flex-col gap-1">
          <h3 id="runtime-policy-title" className="font-semibold">
            {t('title')}
          </h3>
          <p className="moldy-ui-caption text-muted-foreground">{t('description')}</p>
        </div>
        <Badge variant="outline" className="moldy-ui-micro">
          {isRecommended ? t('sourceRecommended') : t('sourceCustom')}
        </Badge>
      </div>

      <fieldset>
        <legend className="sr-only">{t('modeLabel')}</legend>
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant={isRecommended ? 'secondary' : 'outline'}
            size="sm"
            aria-pressed={isRecommended}
            onClick={selectRecommendedPolicy}
          >
            {t('recommended')}
          </Button>
          <Button
            type="button"
            variant={isRecommended ? 'outline' : 'secondary'}
            size="sm"
            aria-pressed={!isRecommended}
            onClick={selectCustomPolicy}
          >
            {t('custom')}
          </Button>
        </div>
      </fieldset>

      <div className="moldy-muted-panel flex flex-col gap-4 p-3">
        <fieldset disabled={controlDisabled} className="flex flex-col gap-4 disabled:opacity-60">
          <legend className="sr-only">{t('custom')}</legend>

          <div className="flex flex-col gap-2">
            <span className="font-medium">{t('todo.title')}</span>
            <div className="flex items-center justify-between gap-3">
              <p className="moldy-ui-caption text-muted-foreground">{t('todo.description')}</p>
              <Switch
                checked={value?.todo.enabled ?? true}
                onCheckedChange={changeTodo}
                aria-label={t('todo.label')}
              />
            </div>
          </div>

          <fieldset className="flex flex-col gap-2">
            <legend className="font-medium">{t('filesystem.title')}</legend>
            <p className="moldy-ui-caption text-muted-foreground">{t('filesystem.description')}</p>
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant={value?.filesystem.mode === 'inspect' ? 'secondary' : 'outline'}
                size="sm"
                aria-pressed={value?.filesystem.mode === 'inspect'}
                onClick={() => changeFilesystem('inspect')}
              >
                {t('filesystem.inspect')}
              </Button>
              <Button
                type="button"
                variant={value?.filesystem.mode === 'artifact_write' ? 'secondary' : 'outline'}
                size="sm"
                aria-pressed={value?.filesystem.mode === 'artifact_write'}
                onClick={() => changeFilesystem('artifact_write')}
              >
                {t('filesystem.artifactWrite')}
              </Button>
            </div>
          </fieldset>

          <fieldset className="flex flex-col gap-2">
            <legend className="font-medium">{t('summarization.title')}</legend>
            <p className="moldy-ui-caption text-muted-foreground">
              {t('summarization.description')}
            </p>
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant={value?.summarization.mode === 'auto' ? 'secondary' : 'outline'}
                size="sm"
                aria-pressed={value?.summarization.mode === 'auto'}
                onClick={() => changeSummarization({ mode: 'auto' })}
              >
                {t('summarization.auto')}
              </Button>
              <Button
                type="button"
                variant={balancedSelected ? 'secondary' : 'outline'}
                size="sm"
                aria-pressed={balancedSelected}
                aria-describedby={contextWindowAvailable ? undefined : balancedHelpId}
                disabled={!contextWindowAvailable}
                onClick={() =>
                  changeSummarization({ mode: 'preset', preset: 'balanced_context_v1' })
                }
              >
                {t('summarization.balanced')}
              </Button>
            </div>
            {!contextWindowAvailable ? (
              <p
                id={balancedHelpId}
                className="moldy-status-surface moldy-status-warn p-2 moldy-ui-caption"
              >
                {invalidBalancedSelection
                  ? t('summarization.invalidCurrent')
                  : t('summarization.unavailable')}
              </p>
            ) : null}
          </fieldset>
        </fieldset>
      </div>

      <p className="moldy-status-surface p-2 moldy-ui-caption">
        {surface === 'existing-agent' ? t('notice.existingAgent') : t('notice.newAgent')}
      </p>
    </section>
  )

  if (!collapsible) return settings

  return (
    <details className="moldy-muted-panel p-3">
      <summary className="cursor-pointer font-medium">{t('advancedTitle')}</summary>
      <div className="pt-3">{settings}</div>
    </details>
  )
}
