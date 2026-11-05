'use client'

import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { ShieldCheck } from 'lucide-react'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { FormFieldShell } from '@/components/shared/form-field-shell'
import { PageHeader } from '@/components/shared/page-header'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { ApiError } from '@/lib/api/client'
import { useSession } from '@/lib/auth/session'
import { cn } from '@/lib/utils'
import { useSystemCredentials } from '@/lib/hooks/use-credentials'
import {
  useGuardrailModelSetting,
  useResetGuardrailModelSetting,
  useTestGuardrailModelSetting,
  useUpdateGuardrailModelSetting,
} from '@/lib/hooks/use-guardrail-model-settings'
import type { Credential } from '@/lib/types/credential'
import {
  GUARDRAIL_MODEL_CREDENTIAL_KEY,
  GUARDRAIL_MODEL_PROBE_STATUSES,
  type GuardrailModelProbeOut,
  type GuardrailModelSettingOut,
} from '@/lib/types/guardrail-model-setting'

const NONE_VALUE = '__none__'
const URL_ERROR_REASONS = [
  'missing',
  'too_long',
  'malformed',
  'scheme_or_host',
  'credentials_in_url',
  'query_or_fragment',
] as const

/**
 * Guardrail model service (guardrail-minimum-plan.md §2.1): turn the Korean model pack on or
 * off, point it at a pii-detector service and test the address. super_user only; the backend
 * enforces it, this guard only avoids 403 noise for a bookmarked URL.
 */
export function GuardrailModelSettingsContent() {
  const t = useTranslations('guardrailModel')
  const router = useRouter()
  const { data: user, isPending } = useSession()
  const denied = !isPending && !!user && !user.is_super_user

  useEffect(() => {
    if (denied) router.replace('/')
  }, [denied, router])

  if (isPending || denied) {
    return <p className="text-sm text-muted-foreground">{t('loading')}</p>
  }
  return <GuardrailModelSettings />
}

function GuardrailModelSettings() {
  const t = useTranslations('guardrailModel')
  const { data: setting, isLoading } = useGuardrailModelSetting()
  const { data: credentials } = useSystemCredentials()
  const bearerCredentials = useMemo(
    () => (credentials ?? []).filter((c) => c.definition_key === GUARDRAIL_MODEL_CREDENTIAL_KEY),
    [credentials],
  )

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t('title')} description={t('description')} />
      <div className="moldy-status-surface moldy-status-warn rounded-lg p-3 text-xs">
        <p className="flex items-center gap-2 font-medium">
          <ShieldCheck className="size-3.5" />
          {t('operatorOnly.title')}
        </p>
        <p className="moldy-status-muted-text mt-1">{t('operatorOnly.description')}</p>
      </div>
      {isLoading || !setting ? (
        <p className="text-sm text-muted-foreground">{t('loading')}</p>
      ) : (
        // Remount on a new server value so the form never keeps a stale copy after a save,
        // a reset or another operator's change.
        <SettingForm
          key={`${setting.source}|${setting.updated_at ?? ''}|${setting.environment_url}`}
          setting={setting}
          credentials={bearerCredentials}
        />
      )}
    </div>
  )
}

function SettingForm({
  setting,
  credentials,
}: {
  setting: GuardrailModelSettingOut
  credentials: Credential[]
}) {
  const t = useTranslations('guardrailModel')
  const update = useUpdateGuardrailModelSetting()
  const reset = useResetGuardrailModelSetting()
  const probe = useTestGuardrailModelSetting()

  const [enabled, setEnabled] = useState(setting.enabled)
  const [url, setUrl] = useState(setting.url)
  const [credentialId, setCredentialId] = useState<string | null>(setting.credential_id)
  const [result, setResult] = useState<GuardrailModelProbeOut | null>(null)
  const [urlError, setUrlError] = useState<string | null>(null)

  const credentialName =
    credentials.find((c) => c.id === credentialId)?.name ?? setting.credential_name
  const dirty =
    enabled !== setting.enabled ||
    url.trim() !== setting.url ||
    credentialId !== setting.credential_id
  const busy = update.isPending || reset.isPending

  function showError(error: unknown, fallback: string) {
    if (error instanceof ApiError && error.code === 'GUARDRAIL_MODEL_INVALID_URL') {
      const reason = URL_ERROR_REASONS.find((r) => r === error.message)
      setUrlError(t(`urlErrors.${reason ?? 'malformed'}`))
      return
    }
    toast.error(fallback)
  }

  async function handleSave() {
    setUrlError(null)
    try {
      await update.mutateAsync({ enabled, url: url.trim(), credential_id: credentialId })
      toast.success(t('toast.saved'))
    } catch (e) {
      showError(e, t('toast.saveFailed'))
    }
  }

  async function handleReset() {
    try {
      await reset.mutateAsync()
      toast.success(t('toast.reset'))
    } catch {
      toast.error(t('toast.resetFailed'))
    }
  }

  async function handleTest() {
    setUrlError(null)
    setResult(null)
    try {
      setResult(await probe.mutateAsync({ url: url.trim(), credential_id: credentialId }))
    } catch (e) {
      showError(e, t('toast.testFailed'))
    }
  }

  return (
    <SettingsSectionCard
      title={t('card.title')}
      description={t('card.description')}
      actions={
        <Badge variant={setting.source === 'saved' ? 'default' : 'outline'}>
          {t(`source.${setting.source}`)}
        </Badge>
      }
    >
      <div className="space-y-5">
        <FormFieldShell
          id="guardrail-model-enabled"
          label={t('enabled.label')}
          description={t('enabled.description')}
          layout="inline"
        >
          <Switch
            id="guardrail-model-enabled"
            checked={enabled}
            onCheckedChange={setEnabled}
            disabled={busy}
          />
        </FormFieldShell>

        <FormFieldShell
          id="guardrail-model-url"
          label={t('url.label')}
          description={
            setting.environment_url
              ? t('url.descriptionWithDefault', { url: setting.environment_url })
              : t('url.description')
          }
          error={urlError ?? undefined}
        >
          <Input
            id="guardrail-model-url"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder={setting.environment_url || t('url.placeholder')}
            disabled={busy}
            className="font-mono"
            spellCheck={false}
            autoComplete="off"
          />
        </FormFieldShell>

        <FormFieldShell
          id="guardrail-model-credential"
          label={t('credential.label')}
          description={t('credential.description')}
        >
          <Select
            value={credentialId ?? NONE_VALUE}
            onValueChange={(value) => setCredentialId(value === NONE_VALUE ? null : value)}
            disabled={busy}
          >
            <SelectTrigger id="guardrail-model-credential" className="w-full">
              <span className="truncate">{credentialName ?? t('credential.none')}</span>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NONE_VALUE}>{t('credential.none')}</SelectItem>
              {credentials.map((c) => (
                <SelectItem key={c.id} value={c.id}>
                  {c.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </FormFieldShell>

        {result ? <ProbeResult result={result} /> : null}
      </div>

      <div className="mt-5 flex flex-wrap items-center justify-end gap-2">
        {setting.source === 'saved' ? (
          <Button variant="ghost" onClick={handleReset} disabled={busy}>
            {reset.isPending ? t('resetting') : t('reset')}
          </Button>
        ) : null}
        <Button variant="outline" onClick={handleTest} disabled={probe.isPending || !url.trim()}>
          {probe.isPending ? t('testing') : t('test')}
        </Button>
        <Button onClick={handleSave} disabled={!dirty || busy}>
          {update.isPending ? t('saving') : t('save')}
        </Button>
      </div>
    </SettingsSectionCard>
  )
}

function ProbeResult({ result }: { result: GuardrailModelProbeOut }) {
  const t = useTranslations('guardrailModel')
  const status = GUARDRAIL_MODEL_PROBE_STATUSES.find((s) => s === result.status) ?? 'bad_response'
  const ok = status === 'ok'
  return (
    <div
      role="status"
      data-testid="guardrail-model-probe"
      className={cn(
        'moldy-status-surface rounded-lg p-3 text-xs',
        ok ? 'moldy-status-success' : 'moldy-status-danger',
      )}
    >
      <p className="font-medium">{t(`probe.${status}`)}</p>
      {ok ? (
        <p className="moldy-status-muted-text mt-1">
          {t('probe.details', {
            model: result.model ?? '-',
            latency: result.latency_ms ?? 0,
            findings: result.findings ?? 0,
          })}
        </p>
      ) : null}
    </div>
  )
}
