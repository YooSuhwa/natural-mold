'use client'

import { useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { Activity, Loader2, Trash2, Zap } from 'lucide-react'

import { announceHealthResult } from '@/lib/health-check-toast'

import { DialogShell } from '@/components/shared/dialog-shell'
import { FormFooter } from '@/components/shared/form-footer'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Checkbox } from '@/components/ui/checkbox'
import { Separator } from '@/components/ui/separator'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { ModelSourceBadge } from './model-source-badge'
import { ModelConnectionTest } from './model-connection-test'
import { RankingsSection } from './model-rankings'
import { StatusChip } from '@/components/shared/status-chip'
import { HealthHistoryChart } from '@/components/shared/health-history-chart'
import { perMillionToTokenPrice, tokenPriceToPerMillion } from './model-format'
import { useDeleteModel, useUpdateModel } from '@/lib/hooks/use-models'
import { useCredentials, useCredentialTypes } from '@/lib/hooks/use-credentials'
import { useModelHealth, useRunHealthCheck } from '@/lib/hooks/use-health'
import { filterLlmCredentials, resolveCredentialForModel } from '@/lib/utils/credential-resolution'
import type { Model } from '@/lib/types/model'

interface ModelEditDialogProps {
  model: Model | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function ModelEditDialog({ model, open, onOpenChange }: ModelEditDialogProps) {
  const t = useTranslations('model')
  const te = useTranslations('model.editDialog')
  const update = useUpdateModel()
  const remove = useDeleteModel()
  const { data: credentials } = useCredentials()
  const { data: definitions } = useCredentialTypes()

  const [displayName, setDisplayName] = useState('')
  const [inputPriceM, setInputPriceM] = useState<string>('')
  const [outputPriceM, setOutputPriceM] = useState<string>('')
  const [contextWindow, setContextWindow] = useState<string>('')
  const [maxOutputTokens, setMaxOutputTokens] = useState<string>('')
  const [supportsVision, setSupportsVision] = useState(false)
  const [supportsTools, setSupportsTools] = useState(false)
  const [supportsReasoning, setSupportsReasoning] = useState(false)
  const [isDefault, setIsDefault] = useState(false)
  const [isVisible, setIsVisible] = useState(true)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [testCredId, setTestCredId] = useState<string>('')
  const [testOpen, setTestOpen] = useState(false)

  const llmCredentials = useMemo(() => {
    if (!credentials || !definitions) return []
    const llmKeys = new Set(definitions.filter((d) => d.category === 'llm').map((d) => d.key))
    return credentials.filter((c) => llmKeys.has(c.definition_key))
  }, [credentials, definitions])

  const effectiveTestCredId = testCredId || llmCredentials[0]?.id || ''

  // Re-seed the form whenever a different model is opened. Sync from props
  // (parent owns the canonical Model row) is the textbook valid use of an
  // effect; the pragma silences the React Compiler heuristic.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (!model) return
    setDisplayName(model.display_name)
    const ip = tokenPriceToPerMillion(model.cost_per_input_token)
    const op = tokenPriceToPerMillion(model.cost_per_output_token)
    setInputPriceM(ip === '' ? '' : String(ip))
    setOutputPriceM(op === '' ? '' : String(op))
    setContextWindow(model.context_window === null ? '' : String(model.context_window))
    setMaxOutputTokens(model.max_output_tokens === null ? '' : String(model.max_output_tokens))
    setSupportsVision(Boolean(model.supports_vision))
    setSupportsTools(Boolean(model.supports_function_calling))
    setSupportsReasoning(Boolean(model.supports_reasoning))
    setIsDefault(model.is_default)
    setIsVisible(model.is_visible)
    setConfirmDelete(false)
    setTestOpen(false)
    setTestCredId('')
  }, [model])
  /* eslint-enable react-hooks/set-state-in-effect */

  if (!model) return null

  async function handleSave() {
    if (!model) return
    try {
      await update.mutateAsync({
        id: model.id,
        data: {
          display_name: displayName.trim() || model.display_name,
          cost_per_input_token: perMillionToTokenPrice(inputPriceM),
          cost_per_output_token: perMillionToTokenPrice(outputPriceM),
          context_window: contextWindow ? Number(contextWindow) : null,
          max_output_tokens: maxOutputTokens ? Number(maxOutputTokens) : null,
          supports_vision: supportsVision,
          supports_function_calling: supportsTools,
          supports_reasoning: supportsReasoning,
          is_default: isDefault,
          is_visible: isVisible,
        },
      })
      toast.success(te('toast.updated'))
      onOpenChange(false)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : te('toast.saveFailed'))
    }
  }

  async function handleDelete() {
    if (!model) return
    try {
      await remove.mutateAsync(model.id)
      toast.success(te('toast.deleted'))
      onOpenChange(false)
    } catch (e) {
      const err = e as { status?: number; message?: string }
      if (err.status === 409) {
        toast.error(
          te('toast.deleteInUse'),
        )
      } else {
        toast.error(err.message ?? te('toast.deleteFailed'))
      }
    }
  }

  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="md" height="fixed">
      <DialogShell.Header
        title={
          <span className="flex items-center gap-2">
            {model.display_name}
            <ModelSourceBadge source={model.source} />
          </span>
        }
        description={
          <span className="font-mono text-xs">
            {model.provider} · {model.model_name}
          </span>
        }
      />
      <DialogShell.Body className="flex flex-col">
        <Tabs defaultValue="info" className="flex min-h-0 w-full flex-1 flex-col">
          <TabsList>
            <TabsTrigger value="info">{te('tabs.info')}</TabsTrigger>
            <TabsTrigger value="health" data-testid="health-tab">
              {te('tabs.health')}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="info" className="flex-1 space-y-4 overflow-y-auto pt-3">
            <RankingsSection
              rankings={model.rankings}
              emptyHint={
                model.source === 'manual'
                  ? te('customIdBenchmarkHint')
                  : undefined
              }
            />

            <div className="flex items-center justify-between gap-2 rounded-md border bg-muted/30 p-2">
              <span className="text-xs text-muted-foreground">
                {te('testHint')}
              </span>
              {!testOpen ? (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setTestOpen(true)}
                  disabled={llmCredentials.length === 0}
                  data-testid="edit-test-button"
                >
                  <Zap className="size-3.5" /> {te('testConnection')}
                </Button>
              ) : (
                <Button variant="ghost" size="sm" onClick={() => setTestOpen(false)}>
                  {te('hideTest')}
                </Button>
              )}
            </div>

            {testOpen && effectiveTestCredId && (
              <div className="space-y-2">
                <Select value={effectiveTestCredId} onValueChange={(v) => v && setTestCredId(v)}>
                  <SelectTrigger className="w-full">
                    <SelectValue placeholder={te('selectCredential')}>
                      {(selected) =>
                        llmCredentials.find((c) => c.id === selected)?.name ?? te('selectCredential')
                      }
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {llmCredentials.map((c) => (
                      <SelectItem key={c.id} value={c.id}>
                        {c.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <ModelConnectionTest
                  key={`${model.id}-${effectiveTestCredId}`}
                  mode="registered"
                  modelId={model.id}
                  credentialId={effectiveTestCredId}
                  modelLabel={model.display_name}
                  autoStart
                />
              </div>
            )}

            <div className="space-y-4">
              <div className="space-y-1.5">
                <label htmlFor="e-display" className="text-xs font-medium">
                  {te('fields.displayName')}
                </label>
                <Input
                  id="e-display"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <label htmlFor="e-input-price" className="text-xs font-medium">
                    {te('fields.inputPrice')}
                  </label>
                  <Input
                    id="e-input-price"
                    type="number"
                    step="0.01"
                    min="0"
                    value={inputPriceM}
                    onChange={(e) => setInputPriceM(e.target.value)}
                  />
                </div>
                <div className="space-y-1.5">
                  <label htmlFor="e-output-price" className="text-xs font-medium">
                    {te('fields.outputPrice')}
                  </label>
                  <Input
                    id="e-output-price"
                    type="number"
                    step="0.01"
                    min="0"
                    value={outputPriceM}
                    onChange={(e) => setOutputPriceM(e.target.value)}
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <label htmlFor="e-context" className="text-xs font-medium">
                    {te('fields.contextWindow')}
                  </label>
                  <Input
                    id="e-context"
                    type="number"
                    step="1024"
                    min="0"
                    value={contextWindow}
                    onChange={(e) => setContextWindow(e.target.value)}
                  />
                </div>
                <div className="space-y-1.5">
                  <label htmlFor="e-max-out" className="text-xs font-medium">
                    {te('fields.maxOutputTokens')}
                  </label>
                  <Input
                    id="e-max-out"
                    type="number"
                    step="256"
                    min="0"
                    value={maxOutputTokens}
                    onChange={(e) => setMaxOutputTokens(e.target.value)}
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <Toggle
                  id="e-vision"
                  label={te('fields.vision')}
                  checked={supportsVision}
                  onChange={setSupportsVision}
                />
                <Toggle
                  id="e-tools"
                  label={te('fields.functionCalling')}
                  checked={supportsTools}
                  onChange={setSupportsTools}
                />
                <Toggle
                  id="e-reasoning"
                  label={te('fields.reasoning')}
                  checked={supportsReasoning}
                  onChange={setSupportsReasoning}
                />
                <Toggle
                  id="e-default"
                  label={te('fields.setDefault')}
                  checked={isDefault}
                  onChange={setIsDefault}
                />
                <Toggle
                  id="e-visible"
                  label={t('visibleToUsers')}
                  checked={isVisible}
                  onChange={setIsVisible}
                />
              </div>

              <Separator />

              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span>
                  {te('usage', { count: model.agent_count })}
                </span>
                {!confirmDelete ? (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setConfirmDelete(true)}
                    className="text-destructive hover:text-destructive"
                  >
                    <Trash2 className="size-3.5" />
                    {te('delete')}
                  </Button>
                ) : (
                  <div className="flex items-center gap-2">
                    <span className="text-destructive">{te('deleteConfirm')}</span>
                    <Button size="sm" variant="outline" onClick={() => setConfirmDelete(false)}>
                      {te('cancel')}
                    </Button>
                    <Button
                      size="sm"
                      variant="destructive"
                      onClick={handleDelete}
                      disabled={remove.isPending}
                    >
                      {te('confirm')}
                    </Button>
                  </div>
                )}
              </div>
            </div>
          </TabsContent>

          <TabsContent value="health" className="flex-1 space-y-3 overflow-y-auto pt-3">
            <ModelHealthPanel
              modelId={model.id}
              provider={model.provider}
              defaultCredentialId={model.default_credential_id}
            />
          </TabsContent>
        </Tabs>
      </DialogShell.Body>
      <DialogShell.Footer>
        <FormFooter
          onCancel={() => onOpenChange(false)}
          onSubmit={handleSave}
          submitLabel={te('save')}
          pending={update.isPending}
        />
      </DialogShell.Footer>
    </DialogShell>
  )
}

function ModelHealthPanel({
  modelId,
  provider,
  defaultCredentialId,
}: {
  modelId: string
  provider: string
  defaultCredentialId: string | null
}) {
  const t = useTranslations('model.editDialog')
  const { data: healthEntries } = useModelHealth()
  const { data: credentials } = useCredentials()
  const runHealthCheck = useRunHealthCheck()

  const llmCredentials = useMemo(() => filterLlmCredentials(credentials), [credentials])
  // Shared resolver — same picks as /models row [Check] and the Test dialog.
  const matchedDefault = useMemo(
    () =>
      resolveCredentialForModel(
        { default_credential_id: defaultCredentialId, provider },
        llmCredentials,
      ) ?? '',
    [llmCredentials, provider, defaultCredentialId],
  )
  // Local override; falls back to the matched default when the user hasn't
  // explicitly picked another credential. Computed at render-time to avoid
  // the lint rule against ``setState`` inside ``useEffect``.
  const [override, setOverride] = useState<string>('')
  const credentialId = override || matchedDefault

  const latest = useMemo(
    () => (healthEntries ?? []).find((h) => h.target_id === modelId),
    [healthEntries, modelId],
  )

  async function handleCheck() {
    if (!credentialId) {
      toast.error(t('toast.noCredential'))
      return
    }
    try {
      const result = await runHealthCheck.mutateAsync({
        targetKind: 'model',
        targetId: modelId,
        credentialId,
      })
      announceHealthResult(result)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('toast.healthCheckFailed'))
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-muted/30 p-3">
        <div className="min-w-0 flex-1 space-y-1">
          <p className="text-xs font-semibold">{t('health.latestProbe')}</p>
          {latest ? (
            <div className="flex flex-wrap items-center gap-2">
              <StatusChip variant={latest.status} />
              {typeof latest.latency_ms === 'number' && (
                <span className="text-xs text-muted-foreground">{latest.latency_ms} ms</span>
              )}
              <span className="moldy-ui-micro text-muted-foreground">
                {new Date(latest.checked_at).toLocaleString()}
              </span>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">{t('health.empty')}</p>
          )}
          {latest?.error_message && (
            <p className="line-clamp-2 break-words moldy-ui-micro text-destructive">
              {latest.error_kind}: {latest.error_message}
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Select value={credentialId} onValueChange={(v) => setOverride(v ?? '')}>
            <SelectTrigger size="sm" className="w-[180px]">
              <SelectValue placeholder={t('selectCredential')}>
                {(selected) =>
                  llmCredentials.find((c) => c.id === selected)?.name ?? t('selectCredential')
                }
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {llmCredentials.map((c) => (
                <SelectItem key={c.id} value={c.id}>
                  {c.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            size="sm"
            variant="outline"
            onClick={handleCheck}
            disabled={runHealthCheck.isPending || !credentialId}
            data-testid="health-check-now"
          >
            {runHealthCheck.isPending ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Activity className="size-3.5" />
            )}
            {t('health.checkNow')}
          </Button>
        </div>
      </div>

      <HealthHistoryChart targetKind="model" targetId={modelId} />
    </div>
  )
}

function Toggle({
  id,
  label,
  checked,
  onChange,
}: {
  id: string
  label: string
  checked: boolean
  onChange: (next: boolean) => void
}) {
  return (
    <label
      htmlFor={id}
      className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm"
    >
      <Checkbox id={id} checked={checked} onCheckedChange={(next) => onChange(Boolean(next))} />
      {label}
    </label>
  )
}
