'use client'

import { useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { AlertTriangleIcon, KeyIcon, ServerIcon } from 'lucide-react'

import { Dialog, DialogContent } from '@/components/ui/dialog'
import { CREDENTIAL_NONE } from '@/components/tool/credential-select'
import { BindingDialogShell } from '@/components/connection/binding-dialog-shell'
import { useCredentials } from '@/lib/hooks/use-credentials'
import {
  scopeKey,
  useConnections,
  useCreateConnection,
  useFindOrCreateCustomConnection,
  useUpdateConnection,
} from '@/lib/hooks/use-connections'
import { useUpdateTool } from '@/lib/hooks/use-tools'
import { ApiError } from '@/lib/api/client'
import {
  CUSTOM_CONNECTION_PROVIDER_NAME as CUSTOM_PROVIDER_NAME,
  PREBUILT_PROVIDER_I18N_KEY as PROVIDER_I18N_KEY,
} from '@/lib/types'
import type { Connection, PrebuiltProviderName, Tool } from '@/lib/types'

interface CommonProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

type PrebuiltProps = CommonProps & {
  type: 'prebuilt'
  providerName: PrebuiltProviderName
  /** Dialog title에 표시할 tool name. 없으면 providerName 사용. */
  toolName?: string
  /**
   * 명시적으로 update 대상 connection. detail drawer 같은 곳에서 selected
   * connection을 직접 갱신할 때 사용.
   */
  connectionId?: string
  /**
   * true이면 항상 새 connection row를 생성한다 (/connections "+ 연결 추가" 흐름).
   * false/미지정이면 default rotate: default 있으면 update, 없으면 create +
   * default 승격.
   */
  createNew?: boolean
  /** @deprecated use onBound. PREBUILT 호출부 후방 호환을 위해 유지. */
  onSaved?: (connection: Connection) => void
  onBound?: (connection: Connection) => void
}

type CustomProps = CommonProps & {
  type: 'custom'
  /** tool-edit 맥락에서 tool row. 현재 connection 해석과 first-bind 판정에 사용. */
  tool?: Tool
  /** tool-edit 맥락에서만. tool.connection_id 보다 우선. */
  currentConnectionId?: string
  /** Dialog title에 표시할 tool name. */
  toolName?: string
  onBound?: (connection: Connection) => void
}

type McpProps = CommonProps & {
  type: 'mcp'
  /** MCP connection row id — credential binding 대상. */
  connectionId: string
  connectionName?: string
  currentCredentialId?: string | null
  onBound?: (connection: Connection) => void
}

type ConnectionBindingDialogProps = PrebuiltProps | CustomProps | McpProps

export function ConnectionBindingDialog(props: ConnectionBindingDialogProps) {
  const { open, onOpenChange } = props
  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) onOpenChange(false)
      }}
    >
      <DialogContent className="sm:max-w-md">
        {open && <BodyDispatch {...props} onClose={() => onOpenChange(false)} />}
      </DialogContent>
    </Dialog>
  )
}

type BodyDispatchProps = ConnectionBindingDialogProps & { onClose: () => void }

function BodyDispatch(props: BodyDispatchProps) {
  if (props.type === 'prebuilt') return <PrebuiltBody {...props} />
  if (props.type === 'custom') return <CustomBody {...props} />
  return <McpBody {...props} />
}

// ─────────────────────────────────────────────────────────────────────
// PREBUILT
// ─────────────────────────────────────────────────────────────────────

function PrebuiltBody({
  providerName,
  toolName,
  connectionId,
  createNew,
  onSaved,
  onBound,
  onClose,
}: PrebuiltProps & { onClose: () => void }) {
  const t = useTranslations('connections.bindingDialog')
  const tProvider = useTranslations('tool.authDialog.provider')

  const qc = useQueryClient()
  const { data: connections, isLoading: connectionsLoading } = useConnections({
    type: 'prebuilt',
    provider_name: providerName,
  })
  const { data: credentials } = useCredentials()
  const createConnection = useCreateConnection()
  const updateConnection = useUpdateConnection()

  const defaultConnection = useMemo(
    () => connections?.find((c) => c.is_default) ?? null,
    [connections],
  )
  // 명시적으로 connectionId가 주어지면 그 row가 hydration / update 대상.
  // 없으면 default connection이 대상 (M3 rotate 패턴).
  const targetConnection = useMemo(
    () =>
      connectionId ? (connections?.find((c) => c.id === connectionId) ?? null) : defaultConnection,
    [connections, connectionId, defaultConnection],
  )
  // render 중 setState + guard (M3 패턴) — useEffect cascading render 회피.
  const hydrationKey = connectionsLoading
    ? null
    : (targetConnection?.id ?? 'empty') + ':' + (targetConnection?.credential_id ?? 'none')
  const [mode, setMode] = useState<string>(CREDENTIAL_NONE)
  const [hydratedFor, setHydratedFor] = useState<string | null>(null)
  if (hydrationKey !== null && hydrationKey !== hydratedFor) {
    setHydratedFor(hydrationKey)
    setMode(targetConnection?.credential_id ?? CREDENTIAL_NONE)
  }

  const matchingCredentials = useMemo(
    () => credentials?.filter((c) => c.provider_name === providerName) ?? [],
    [credentials, providerName],
  )

  const providerI18nKey = PROVIDER_I18N_KEY[providerName]
  const displayTitle = toolName ?? providerName
  const isPending = createConnection.isPending || updateConnection.isPending

  // explicitTarget이 있으면 그 row를 direct update.
  // 없을 때 createNew=true이거나 default가 없으면 create branch.
  const explicitTarget = connectionId ? targetConnection : null

  async function handleSave() {
    const credentialId = mode === CREDENTIAL_NONE ? null : mode
    try {
      let result: Connection
      if (explicitTarget) {
        // 명시 target의 직접 갱신. default 여부와 무관 — drawer rebind 흐름.
        result = await updateConnection.mutateAsync({
          id: explicitTarget.id,
          data: { credential_id: credentialId, status: 'active' },
        })
      } else if (!createNew && defaultConnection) {
        // credential 재바인딩 = 재활성화 의도. disabled default도 저장 후 active로.
        result = await updateConnection.mutateAsync({
          id: defaultConnection.id,
          data: { credential_id: credentialId, status: 'active' },
        })
      } else {
        if (credentialId === null) {
          onClose()
          return
        }
        result = await createConnection.mutateAsync({
          type: 'prebuilt',
          provider_name: providerName,
          display_name: toolName ?? providerName,
          credential_id: credentialId,
          // 첫 connection은 default. 기존 default가 이미 있으면 새 row는 non-default
          // (user가 detail sheet에서 default 토글로 승격 가능).
          is_default: !defaultConnection,
        })
      }
      toast.success(t('toast.saved'))
      onSaved?.(result)
      onBound?.(result)
      onClose()
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        qc.invalidateQueries({ queryKey: ['connections', 'prebuilt', providerName] })
        qc.invalidateQueries({ queryKey: ['connections'] })
        toast.error(t('toast.conflictRetry'))
        return
      }
      toast.error(t('toast.saveFailed'))
    }
  }

  return (
    <BindingDialogShell
      icon={<KeyIcon className="size-4" />}
      title={t('title', { name: displayTitle })}
      description={
        <>
          {t('description')}
          {providerI18nKey && tProvider(providerI18nKey)}
        </>
      }
      mode={mode}
      onModeChange={setMode}
      credentials={matchingCredentials}
      connectionsLoading={connectionsLoading}
      createFormDefaultProvider={providerName}
      onCredentialCreated={(c) => {
        if (c.provider_name === providerName) setMode(c.id)
      }}
      onSave={handleSave}
      onCancel={onClose}
      isPending={isPending}
    />
  )
}

// ─────────────────────────────────────────────────────────────────────
// CUSTOM
// ─────────────────────────────────────────────────────────────────────

function CustomBody({
  tool,
  currentConnectionId,
  toolName,
  onBound,
  onClose,
}: CustomProps & { onClose: () => void }) {
  const t = useTranslations('connections.bindingDialog')

  const qc = useQueryClient()
  const { data: credentials } = useCredentials()
  const { data: connections, isLoading: connectionsLoading } = useConnections({
    type: 'custom',
    provider_name: CUSTOM_PROVIDER_NAME,
  })
  const findOrCreate = useFindOrCreateCustomConnection()
  const updateConnection = useUpdateConnection()
  const updateTool = useUpdateTool()

  const effectiveConnectionId = currentConnectionId ?? tool?.connection_id ?? null
  const currentConnection = useMemo(
    () => connections?.find((c) => c.id === effectiveConnectionId) ?? null,
    [connections, effectiveConnectionId],
  )

  // 하이드레이션: 초기 선택값은 현재 connection의 credential. 없으면 none.
  const hydrationKey = connectionsLoading
    ? null
    : (currentConnection?.id ?? 'empty') + ':' + (currentConnection?.credential_id ?? 'none')
  const [mode, setMode] = useState<string>(CREDENTIAL_NONE)
  const [hydratedFor, setHydratedFor] = useState<string | null>(null)
  if (hydrationKey !== null && hydrationKey !== hydratedFor) {
    setHydratedFor(hydrationKey)
    setMode(currentConnection?.credential_id ?? CREDENTIAL_NONE)
  }

  const availableCredentials = credentials ?? []
  const isPending =
    findOrCreate.isPending || updateConnection.isPending || updateTool.isPending

  async function handleSave() {
    const credentialId = mode === CREDENTIAL_NONE ? null : mode
    try {
      let result: Connection
      if (currentConnection) {
        // 기존 connection의 credential rotate.
        result = await updateConnection.mutateAsync({
          id: currentConnection.id,
          data: { credential_id: credentialId, status: 'active' },
        })
      } else {
        if (credentialId === null) {
          onClose()
          return
        }
        const credential = availableCredentials.find((c) => c.id === credentialId)
        result = await findOrCreate.run(
          credentialId,
          credential?.name ?? toolName ?? 'Custom connection',
        )
        // first-bind: tool이 바인딩이 없으면 PATCH /api/tools/{id}로 connection_id 연결.
        // ADR-008 N:1 — findOrCreate가 기존 connection을 재사용해도 tool이 새로 붙을 뿐이라 안전.
        if (tool && !tool.connection_id) {
          await updateTool.mutateAsync({
            id: tool.id,
            data: { connection_id: result.id },
          })
        }
      }
      toast.success(t('toast.saved'))
      onBound?.(result)
      onClose()
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        qc.invalidateQueries({
          queryKey: scopeKey({ type: 'custom', provider_name: CUSTOM_PROVIDER_NAME }),
        })
        qc.invalidateQueries({ queryKey: ['connections'] })
        toast.error(t('toast.conflictRetry'))
        return
      }
      toast.error(t('toast.saveFailed'))
    }
  }

  const displayTitle = toolName ?? tool?.name ?? ''

  return (
    <BindingDialogShell
      icon={<KeyIcon className="size-4" />}
      title={t('custom.title', { toolName: displayTitle })}
      description={t('custom.description')}
      mode={mode}
      onModeChange={setMode}
      credentials={availableCredentials}
      connectionsLoading={connectionsLoading}
      onCredentialCreated={(c) => setMode(c.id)}
      onSave={handleSave}
      onCancel={onClose}
      isPending={isPending}
    />
  )
}

// ─────────────────────────────────────────────────────────────────────
// MCP — connection credential binding (단일 PATCH)
// ─────────────────────────────────────────────────────────────────────

function McpBody({
  connectionId,
  connectionName,
  currentCredentialId,
  onBound,
  onClose,
}: McpProps & { onClose: () => void }) {
  const t = useTranslations('connections.bindingDialog')

  const { data: credentials } = useCredentials()
  const { data: mcpConnections } = useConnections({ type: 'mcp' })
  const updateConnection = useUpdateConnection()
  const [mode, setMode] = useState<string>(currentCredentialId ?? CREDENTIAL_NONE)

  const availableCredentials = credentials ?? []

  // N:1 공유 (여러 MCP connection이 같은 credential을 공유하는 경우) 경고. credential
  // rotate는 이 connection에만 적용되나, 사용자가 혼동하지 않도록 안내한다.
  const sharedAcrossConnections = useMemo(() => {
    if (!currentCredentialId || !mcpConnections) return false
    const siblings = mcpConnections.filter(
      (c) => c.credential_id === currentCredentialId && c.id !== connectionId,
    )
    return siblings.length > 0
  }, [mcpConnections, currentCredentialId, connectionId])

  async function handleSave() {
    const credentialId = mode === CREDENTIAL_NONE ? null : mode
    try {
      const updated = await updateConnection.mutateAsync({
        id: connectionId,
        data: { credential_id: credentialId, status: 'active' },
      })
      toast.success(
        sharedAcrossConnections ? t('toast.savedSharedCredential') : t('toast.saved'),
      )
      onBound?.(updated)
      onClose()
    } catch {
      toast.error(t('toast.saveFailed'))
    }
  }

  const topSlot = sharedAcrossConnections ? (
    <div
      role="alert"
      className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 p-2.5 text-xs text-amber-800"
    >
      <AlertTriangleIcon className="mt-0.5 size-3.5 shrink-0" />
      <span>{t('mcp.sharedCredentialWarning')}</span>
    </div>
  ) : undefined

  return (
    <BindingDialogShell
      icon={<ServerIcon className="size-4" />}
      title={t('mcp.title', { serverName: connectionName ?? '' })}
      description={t('mcp.description')}
      topSlot={topSlot}
      mode={mode}
      onModeChange={setMode}
      credentials={availableCredentials}
      onCredentialCreated={(c) => setMode(c.id)}
      onSave={handleSave}
      onCancel={onClose}
      isPending={updateConnection.isPending}
    />
  )
}
