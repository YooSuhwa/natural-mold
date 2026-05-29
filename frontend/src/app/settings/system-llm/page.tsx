'use client'

import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { SlidersHorizontal } from 'lucide-react'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from '@/components/ui/select'
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
  type SystemLlmRole,
  type SystemLlmSettingOut,
} from '@/lib/types/system-llm-setting'

const ROLE_LABELS: Record<SystemLlmRole, string> = {
  text_primary: '텍스트 기본 모델',
  text_fallback: '텍스트 폴백 모델',
  image: '이미지 모델',
}

const ROLE_DESCRIPTIONS: Record<SystemLlmRole, string> = {
  text_primary: 'Builder/Assistant가 사용하는 기본 텍스트 LLM.',
  text_fallback: '기본 모델 호출이 실패할 때 사용하는 폴백 텍스트 LLM.',
  image: '이미지 생성에 사용하는 모델 (base_url 주입 지원).',
}

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
  const router = useRouter()
  const { data: user, isPending } = useSession()
  const denied = !isPending && !!user && !user.is_super_user

  useEffect(() => {
    if (denied) router.replace('/')
  }, [denied, router])

  if (isPending || denied) {
    return (
      <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 overflow-auto p-6">
        <p className="text-sm text-muted-foreground">불러오는 중…</p>
      </div>
    )
  }

  return <SystemLlmSettingsPageInner />
}

function SystemLlmSettingsPageInner() {
  const { data: settings, isLoading } = useSystemLlmSettings()
  const { data: credentials } = useSystemCredentials()

  const llmCredentials = useMemo(
    () =>
      (credentials ?? []).filter((c) =>
        LLM_CREDENTIAL_KEYS.includes(c.definition_key),
      ),
    [credentials],
  )

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 overflow-auto p-6">
      <PageHeader
        title="System LLM 설정"
        description="Builder·Assistant·이미지 생성이 사용하는 시스템 모델을 역할별로 선택합니다. 자격증명 등록은 시스템 자격증명 화면에서 진행하세요."
      />

      <div className="rounded-lg border bg-amber-50/40 p-3 text-xs text-amber-900 dark:border-amber-900/30 dark:bg-amber-950/20 dark:text-amber-200">
        <p className="flex items-center gap-2 font-medium">
          <SlidersHorizontal className="size-3.5" /> 운영자 전용
        </p>
        <p className="mt-1 text-amber-800/80 dark:text-amber-200/70">
          세 슬롯을 모두 설정하기 전까지 Builder/Assistant/이미지 생성이
          동작하지 않습니다. 제공자는 선택한 시스템 자격증명에서 자동으로
          파생되며, openai_compatible/openrouter는 자격증명의 base_url을
          그대로 사용합니다.
        </p>
      </div>

      {isLoading || !settings ? (
        <p className="text-sm text-muted-foreground">불러오는 중…</p>
      ) : (
        <div className="grid gap-4">
          {settings.map((setting) => (
            <SlotCard
              key={setting.role}
              setting={setting}
              credentials={llmCredentials}
            />
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
  const update = useUpdateSystemLlmSetting()
  const discover = useDiscoverModels()

  const [credentialId, setCredentialId] = useState<string | null>(
    setting.credential_id,
  )
  const [modelName, setModelName] = useState<string | null>(setting.model_name)
  const [models, setModels] = useState<DiscoveredModel[]>([])

  const selectedCredential = credentials.find((c) => c.id === credentialId)
  const provider = selectedCredential?.definition_key ?? setting.provider

  function loadModels(id: string) {
    discover.mutate(id, {
      onSuccess: (list) => setModels(list),
      onError: (e) =>
        toast.error(e instanceof Error ? e.message : '모델 목록 로드 실패'),
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

  const dirty =
    credentialId !== setting.credential_id || modelName !== setting.model_name
  const canSave = dirty && (credentialId === null || !!modelName)

  async function handleSave() {
    try {
      await update.mutateAsync({
        role: setting.role,
        data: { credential_id: credentialId, model_name: modelName },
      })
      toast.success(`${ROLE_LABELS[setting.role]} 저장 완료`)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '저장 실패')
    }
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-base">
            {ROLE_LABELS[setting.role]}
          </CardTitle>
          {setting.configured ? (
            <Badge variant="default">설정됨</Badge>
          ) : (
            <Badge variant="outline">미설정</Badge>
          )}
        </div>
        <CardDescription>{ROLE_DESCRIPTIONS[setting.role]}</CardDescription>
      </CardHeader>

      <CardContent className="space-y-4">
        <div className="grid gap-2 rounded-lg border border-border/60 bg-muted/30 p-3 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs font-medium text-muted-foreground">자격증명</span>
            <span className="font-medium text-foreground">
              {selectedCredentialName ?? '선택 안 함'}
            </span>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs font-medium text-muted-foreground">모델</span>
            <span className="font-mono text-xs text-foreground">
              {selectedModelLabel ?? '선택 안 함'}
            </span>
          </div>
        </div>

        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">
            시스템 자격증명
          </label>
          <Select
            value={credentialId ?? NONE_VALUE}
            onValueChange={handleCredentialChange}
          >
            <SelectTrigger className="w-full">
              <span className="truncate">
                {selectedCredentialName ?? '자격증명 선택'}
              </span>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NONE_VALUE}>선택 안 함</SelectItem>
              {credentials.map((c) => (
                <SelectItem key={c.id} value={c.id}>
                  {c.name} · {c.definition_key}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {credentials.length === 0 && (
            <p className="text-xs text-muted-foreground">
              사용 가능한 LLM 시스템 자격증명이 없습니다. 시스템 자격증명
              화면에서 먼저 등록하세요.
            </p>
          )}
        </div>

        <div className="space-y-1.5">
          <div className="flex items-center justify-between gap-2">
            <label className="text-xs font-medium text-muted-foreground">
              모델
            </label>
            {credentialId && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => loadModels(credentialId)}
                disabled={discover.isPending}
              >
                {discover.isPending ? '불러오는 중…' : '모델 목록 불러오기'}
              </Button>
            )}
          </div>
          <Select
            value={modelName ?? ''}
            onValueChange={(value) => setModelName(value)}
            disabled={!credentialId || modelOptions.length === 0}
          >
            <SelectTrigger className="w-full">
              <span className="truncate">{selectedModelLabel ?? '모델 선택'}</span>
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
            <p className="text-xs text-muted-foreground">
              먼저 시스템 자격증명을 선택하세요.
            </p>
          ) : discover.isError ? (
            <p className="text-xs text-destructive">
              모델 목록을 불러오지 못했습니다. ‘모델 목록 불러오기’로 다시
              시도하세요.
            </p>
          ) : !discover.isPending && modelOptions.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              이 자격증명에서 사용 가능한 모델이 없습니다.
            </p>
          ) : null}
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
          {provider && (
            <span>
              제공자 <span className="font-mono">{provider}</span>
            </span>
          )}
          {setting.base_url && (
            <span>
              Base URL <span className="font-mono">{setting.base_url}</span>
            </span>
          )}
        </div>
      </CardContent>

      <CardContent className="flex justify-end pt-0">
        <Button onClick={handleSave} disabled={!canSave || update.isPending}>
          {update.isPending ? '저장 중…' : '저장'}
        </Button>
      </CardContent>
    </Card>
  )
}
