'use client'

import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { SlidersHorizontal } from 'lucide-react'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger } from '@/components/ui/select'
import { PageHeader } from '@/components/shared/page-header'
import { useSession } from '@/lib/auth/session'
import { useSystemCredentials } from '@/lib/hooks/use-credentials'
import { useDiscoverModels } from '@/lib/hooks/use-models'
import {
  useSystemLlmSettings,
  useUpdateSystemLlmSetting,
} from '@/lib/hooks/use-system-llm-settings'
import type { Credential } from '@/lib/types/credential'
import type { DiscoveredModel } from '@/lib/types/model'
import {
  SYSTEM_LLM_CREDENTIAL_KEYS,
  type SystemLlmSettingOut,
} from '@/lib/types/system-llm-setting'
import { SettingsShell } from '../_components/settings-shell'

const NONE_VALUE = '__none__'

const LLM_CREDENTIAL_KEYS = SYSTEM_LLM_CREDENTIAL_KEYS as readonly string[]

/**
 * System LLM Settings — operators pick a System Credential + model for each
 * role slot (text_primary / text_fallback / image). super_user only:
 * Builder/Assistant/image generation read these at runtime (ADR-019, no .env
 * fallback). Credential registration stays on the System Credentials screen.
 *
 * Backend enforces `require_super_user` on every endpoint; this guard hides
 * the chrome and avoids 403 noise for users who land via a bookmarked URL.
 */
export default function SystemLlmSettingsPage() {
  const t = useTranslations('systemLlm')
  const router = useRouter()
  const { data: user, isPending } = useSession()
  const denied = !isPending && !!user && !user.is_super_user

  useEffect(() => {
    if (denied) router.replace('/')
  }, [denied, router])

  if (isPending || denied) {
    return (
      <SettingsShell>
        <p className="text-sm text-muted-foreground">{t('loading')}</p>
      </SettingsShell>
    )
  }

  return (
    <SettingsShell>
      <SystemLlmSettingsPageInner />
    </SettingsShell>
  )
}

function SystemLlmSettingsPageInner() {
  const t = useTranslations('systemLlm')
  const { data: settings, isLoading } = useSystemLlmSettings()
  const { data: credentials } = useSystemCredentials()

  const llmCredentials = useMemo(
    () => (credentials ?? []).filter((c) => LLM_CREDENTIAL_KEYS.includes(c.definition_key)),
    [credentials],
  )

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t('title')} description={t('description')} />

      <div className="moldy-status-surface moldy-status-warn rounded-lg p-3 text-xs">
        <p className="flex items-center gap-2 font-medium">
          <SlidersHorizontal className="size-3.5" />
          {t('operatorOnly.title')}
        </p>
        <p className="moldy-status-muted-text mt-1">{t('operatorOnly.description')}</p>
      </div>

      {isLoading || !settings ? (
        <p className="text-sm text-muted-foreground">{t('loading')}</p>
      ) : (
        <div className="grid gap-4">
          {settings.map((setting) => (
            <SlotCard key={setting.role} setting={setting} credentials={llmCredentials} />
          ))}
        </div>
      )}
    </div>
  )
}

function SlotCard({
  setting,
  credentials,
}: {
  setting: SystemLlmSettingOut
  credentials: Credential[]
}) {
  const t = useTranslations('systemLlm')
  const update = useUpdateSystemLlmSetting()
  const discover = useDiscoverModels()

  const [credentialId, setCredentialId] = useState<string | null>(setting.credential_id)
  const [modelName, setModelName] = useState<string | null>(setting.model_name)
  const [models, setModels] = useState<DiscoveredModel[]>([])

  const selectedCredential = credentials.find((c) => c.id === credentialId)
  const provider = selectedCredential?.definition_key ?? setting.provider

  function loadModels(id: string) {
    discover.mutate(id, {
      onSuccess: (list) => setModels(list),
      onError: (e) => toast.error(e instanceof Error ? e.message : t('toast.loadModelsFailed')),
    })
  }

  function handleCredentialChange(value: string | null) {
    const id = value === NONE_VALUE || value === null ? null : value
    setCredentialId(id)
    setModelName(null)
    setModels([])
    if (id) loadModels(id)
  }

  // Discovered models, ensuring the currently-saved model stays selectable
  // even before the operator re-runs discovery.
  const modelOptions = useMemo(() => {
    const names = models.map((m) => m.model_name)
    if (modelName && !names.includes(modelName)) return [modelName, ...names]
    return names
  }, [models, modelName])

  const modelLabels = useMemo(() => {
    const map = new Map<string, string>()
    models.forEach((m) => map.set(m.model_name, m.display_name))
    return map
  }, [models])
  const selectedCredentialName = selectedCredential?.name ?? setting.credential_name
  const selectedModelLabel = modelName ? (modelLabels.get(modelName) ?? modelName) : null

  const dirty = credentialId !== setting.credential_id || modelName !== setting.model_name
  const canSave = dirty && (credentialId === null || !!modelName)

  async function handleSave() {
    try {
      await update.mutateAsync({
        role: setting.role,
        data: { credential_id: credentialId, model_name: modelName },
      })
      toast.success(t('toast.saved', { role: t(`roles.${setting.role}.label`) }))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('toast.saveFailed'))
    }
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-base">{t(`roles.${setting.role}.label`)}</CardTitle>
          {setting.configured ? (
            <Badge variant="default">{t('configured')}</Badge>
          ) : (
            <Badge variant="outline">{t('notConfigured')}</Badge>
          )}
        </div>
        <CardDescription>{t(`roles.${setting.role}.description`)}</CardDescription>
      </CardHeader>

      <CardContent className="space-y-4">
        <div className="grid gap-2 rounded-lg border border-border/60 bg-muted/30 p-3 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs font-medium text-muted-foreground">{t('credential')}</span>
            <span className="font-medium text-foreground">
              {selectedCredentialName ?? t('none')}
            </span>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs font-medium text-muted-foreground">{t('model')}</span>
            <span className="font-mono text-xs text-foreground">
              {selectedModelLabel ?? t('none')}
            </span>
          </div>
        </div>

        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">
            {t('systemCredential')}
          </label>
          <Select value={credentialId ?? NONE_VALUE} onValueChange={handleCredentialChange}>
            <SelectTrigger className="w-full">
              <span className="truncate">{selectedCredentialName ?? t('selectCredential')}</span>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NONE_VALUE}>{t('none')}</SelectItem>
              {credentials.map((c) => (
                <SelectItem key={c.id} value={c.id}>
                  {c.name} · {c.definition_key}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {credentials.length === 0 && (
            <p className="text-xs text-muted-foreground">{t('emptyCredentials')}</p>
          )}
        </div>

        <div className="space-y-1.5">
          <div className="flex items-center justify-between gap-2">
            <label className="text-xs font-medium text-muted-foreground">{t('model')}</label>
            {credentialId && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => loadModels(credentialId)}
                disabled={discover.isPending}
              >
                {discover.isPending ? t('loading') : t('loadModels')}
              </Button>
            )}
          </div>
          <Select
            value={modelName ?? ''}
            onValueChange={(value) => setModelName(value)}
            disabled={!credentialId || modelOptions.length === 0}
          >
            <SelectTrigger className="w-full">
              <span className="truncate">{selectedModelLabel ?? t('selectModel')}</span>
            </SelectTrigger>
            <SelectContent>
              {modelOptions.map((name) => (
                <SelectItem key={name} value={name}>
                  {modelLabels.get(name) ?? name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {!credentialId ? (
            <p className="text-xs text-muted-foreground">{t('selectCredentialFirst')}</p>
          ) : discover.isError ? (
            <p className="text-xs text-destructive">{t('modelLoadFailed')}</p>
          ) : !discover.isPending && modelOptions.length === 0 ? (
            <p className="text-xs text-muted-foreground">{t('noModels')}</p>
          ) : null}
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
          {provider && (
            <span>
              {t('provider')} <span className="font-mono">{provider}</span>
            </span>
          )}
          {setting.base_url && (
            <span>
              {t('baseUrl')} <span className="font-mono">{setting.base_url}</span>
            </span>
          )}
        </div>
      </CardContent>

      <CardContent className="flex justify-end pt-0">
        <Button onClick={handleSave} disabled={!canSave || update.isPending}>
          {update.isPending ? t('saving') : t('save')}
        </Button>
      </CardContent>
    </Card>
  )
}
