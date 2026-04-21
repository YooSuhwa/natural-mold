'use client'

import { useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  AlertTriangleIcon,
  CheckCircleIcon,
  KeyIcon,
  LinkIcon,
  Loader2Icon,
  ServerIcon,
} from 'lucide-react'

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { CredentialFormDialog } from '@/components/tool/credential-form-dialog'
import { CredentialSelect, CREDENTIAL_NONE } from '@/components/tool/credential-select'
import { useCredentials } from '@/lib/hooks/use-credentials'
import {
  scopeKey,
  useConnections,
  useCreateConnection,
  useFindOrCreateCustomConnection,
  useUpdateConnection,
} from '@/lib/hooks/use-connections'
import { useUpdateMCPServer } from '@/lib/hooks/use-tools'
import { ApiError } from '@/lib/api/client'
import {
  CUSTOM_CONNECTION_PROVIDER_NAME as CUSTOM_PROVIDER_NAME,
  PREBUILT_PROVIDER_I18N_KEY as PROVIDER_I18N_KEY,
} from '@/lib/types'
import type { Connection, PrebuiltProviderName, Tool } from '@/lib/types'

interface CommonProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Dialog 진입 맥락 — i18n/완료 동작 분기. 미지정 시 'standalone'. */
  triggerContext?: 'tool-create' | 'tool-edit' | 'standalone'
}

type PrebuiltProps = CommonProps & {
  type: 'prebuilt'
  providerName: PrebuiltProviderName
  /** Dialog title에 표시할 tool name. 없으면 providerName 사용. */
  toolName?: string
  /**
   * 명시적으로 update 대상 connection. detail drawer 같은 곳에서 selected
   * connection을 직접 갱신할 때 사용. 미지정 시 default rotate 또는 신규 생성
   * 휴리스틱(`triggerContext`)을 적용한다.
   */
  connectionId?: string
  /** @deprecated use onBound. PREBUILT 호출부 후방 호환을 위해 유지. */
  onSaved?: (connection: Connection) => void
  onBound?: (connection: Connection) => void
}

type CustomProps = CommonProps & {
  type: 'custom'
  /** tool-edit 맥락에서 tool row. bridge override 판정과 현재 connection 해석에 사용. */
  tool?: Tool
  /** tool-edit 맥락에서만. tool.connection_id 보다 우선. */
  currentConnectionId?: string
  /** Dialog title에 표시할 tool name. */
  toolName?: string
  onBound?: (connection: Connection) => void
}

type McpProps = CommonProps & {
  type: 'mcp'
  /** mcp_servers row id — credential binding 대상. 필수. */
  mcpServerId: string
  serverName?: string
  currentCredentialId?: string | null
  /** MCP는 Connection 엔티티를 만들지 않는다 — server row의 credential만 갱신. */
  onBound?: (result: { serverId: string; credentialId: string | null }) => void
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
  triggerContext = 'standalone',
  connectionId,
  onSaved,
  onBound,
  onClose,
}: PrebuiltProps & { onClose: () => void }) {
  const t = useTranslations('connections.bindingDialog')
  const tProvider = useTranslations('tool.authDialog.provider')
  const tc = useTranslations('common')
  const tCred = useTranslations('connections.credentialSelect')

  const qc = useQueryClient()
  const { data: connections, isLoading: connectionsLoading } = useConnections({
    type: 'prebuilt',
    provider_name: providerName,
  })
  const { data: credentials } = useCredentials()
  const createConnection = useCreateConnection()
  const updateConnection = useUpdateConnection()
  const [createOpen, setCreateOpen] = useState(false)

  const defaultConnection = useMemo(
    () => connections?.find((c) => c.is_default) ?? null,
    [connections],
  )
  // 명시적으로 connectionId가 주어지면 그 row가 hydration / update 대상.
  // 없으면 default connection이 대상 (M3 rotate 패턴).
  const targetConnection = useMemo(
    () => (connectionId ? (connections?.find((c) => c.id === connectionId) ?? null) : defaultConnection),
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

  // 명시적 connectionId가 있으면 항상 그 row를 update (drawer rebind 흐름).
  // 없으면 standalone="연결 추가"는 신규 생성, tool-edit은 default rotate.
  const explicitTarget = connectionId ? targetConnection : null
  const shouldCreateNew =
    explicitTarget != null
      ? false
      : triggerContext === 'standalone'
        ? !!defaultConnection
        : !defaultConnection

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
      } else if (!shouldCreateNew && defaultConnection) {
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
    <>
      <DialogHeader>
        <DialogTitle className="flex items-center gap-2">
          <KeyIcon className="size-4" />
          {t('title', { name: displayTitle })}
        </DialogTitle>
        <DialogDescription>
          {t('description')}
          {providerI18nKey && tProvider(providerI18nKey)}
        </DialogDescription>
      </DialogHeader>

      <div className="space-y-4 py-2">
        <div className="space-y-2">
          <label className="text-sm font-medium flex items-center gap-1.5">
            <LinkIcon className="size-3.5" />
            {tCred('label')}
          </label>
          {connectionsLoading ? (
            <Skeleton className="h-9 w-full" />
          ) : (
            <CredentialSelect
              value={mode}
              onValueChange={setMode}
              onCreateRequested={() => setCreateOpen(true)}
              credentials={matchingCredentials}
            />
          )}
        </div>

        {mode !== CREDENTIAL_NONE && (
          <div className="flex items-center gap-2 text-xs text-emerald-600">
            <CheckCircleIcon className="size-3.5" />
            {t('configured')}
          </div>
        )}
      </div>

      <DialogFooter>
        <Button variant="outline" onClick={onClose}>
          {tc('cancel')}
        </Button>
        <Button onClick={handleSave} disabled={isPending}>
          {isPending && (
            <Loader2Icon className="size-4 animate-spin" data-icon="inline-start" />
          )}
          {tc('save')}
        </Button>
      </DialogFooter>

      <CredentialFormDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        defaultProvider={providerName}
        onCreated={(c) => {
          if (c.provider_name === providerName) setMode(c.id)
        }}
      />
    </>
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
  const tc = useTranslations('common')
  const tCred = useTranslations('connections.credentialSelect')

  const qc = useQueryClient()
  const { data: credentials } = useCredentials()
  const { data: connections, isLoading: connectionsLoading } = useConnections({
    type: 'custom',
    provider_name: CUSTOM_PROVIDER_NAME,
  })
  const findOrCreate = useFindOrCreateCustomConnection()
  const updateConnection = useUpdateConnection()
  const [createOpen, setCreateOpen] = useState(false)

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
  const isPending = findOrCreate.isPending || updateConnection.isPending

  // 기존 tool인데 connection이 없는 경우(첫 바인딩) runtime이 fail-closed인데
  // tool.connection_id를 쓰는 API가 없어 수리 경로가 없다. 옵션 D가 들어오는
  // M6.1까지는 UX로 차단.
  const needsOptionDFirstBind = !!tool && !tool.connection_id
  const saveDisabled = isPending || needsOptionDFirstBind

  async function handleSave() {
    if (needsOptionDFirstBind) {
      toast.error(t('toast.unsupportedFirstBindM6'))
      return
    }
    const credentialId = mode === CREDENTIAL_NONE ? null : mode
    try {
      let result: Connection
      if (currentConnection) {
        // 기존 connection의 credential rotate. (M6 이후 tool.credential_id 컬럼 자체 삭제됨.)
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
    <>
      <DialogHeader>
        <DialogTitle className="flex items-center gap-2">
          <KeyIcon className="size-4" />
          {t('custom.title', { toolName: displayTitle })}
        </DialogTitle>
        <DialogDescription>{t('custom.description')}</DialogDescription>
      </DialogHeader>

      <div className="space-y-4 py-2">
        <div className="space-y-2">
          <label className="text-sm font-medium flex items-center gap-1.5">
            <LinkIcon className="size-3.5" />
            {tCred('label')}
          </label>
          {connectionsLoading ? (
            <Skeleton className="h-9 w-full" />
          ) : (
            <CredentialSelect
              value={mode}
              onValueChange={setMode}
              onCreateRequested={() => setCreateOpen(true)}
              credentials={availableCredentials}
            />
          )}
        </div>

        {mode !== CREDENTIAL_NONE && (
          <div className="flex items-center gap-2 text-xs text-emerald-600">
            <CheckCircleIcon className="size-3.5" />
            {t('configured')}
          </div>
        )}
      </div>

      {needsOptionDFirstBind && (
        <div
          role="alert"
          className="mb-4 flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-xs text-amber-800"
        >
          <AlertTriangleIcon className="mt-0.5 size-3.5 shrink-0" />
          <span>{t('custom.unsupportedFirstBindM6')}</span>
        </div>
      )}

      <DialogFooter>
        <Button variant="outline" onClick={onClose}>
          {tc('cancel')}
        </Button>
        <Button onClick={handleSave} disabled={saveDisabled}>
          {isPending && (
            <Loader2Icon className="size-4 animate-spin" data-icon="inline-start" />
          )}
          {tc('save')}
        </Button>
      </DialogFooter>

      <CredentialFormDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={(c) => setMode(c.id)}
      />
    </>
  )
}

// ─────────────────────────────────────────────────────────────────────
// MCP — credential binding only (server row PATCH)
// ─────────────────────────────────────────────────────────────────────

function McpBody({
  mcpServerId,
  serverName,
  currentCredentialId,
  onBound,
  onClose,
}: McpProps & { onClose: () => void }) {
  const t = useTranslations('connections.bindingDialog')
  const tc = useTranslations('common')
  const tCred = useTranslations('connections.credentialSelect')

  const { data: credentials } = useCredentials()
  const { data: mcpConnections } = useConnections({ type: 'mcp' })
  const updateServer = useUpdateMCPServer()
  const updateConnection = useUpdateConnection()
  const [createOpen, setCreateOpen] = useState(false)
  const [mode, setMode] = useState<string>(currentCredentialId ?? CREDENTIAL_NONE)

  const availableCredentials = credentials ?? []

  // chat_service는 `tool.connection.credential_id`를 SOT로 사용한다. server PATCH만으로는
  // runtime이 stale → connection도 동기 갱신해야 한다. **단일 connection이 이 server만 가리키는
  // 경우(linkedConnections.length === 1)에 한해 안전하게 sync한다.** N:1 공유는 cross-server
  // mutation이 되므로 차단 + 사용자 안내. 진짜 정공법(새 connection find-or-create + tool.connection_id
  // PATCH)은 M6 cleanup(`mcp_servers` drop과 통합) 영역.
  const linkedConnections = useMemo(
    () =>
      currentCredentialId
        ? (mcpConnections?.filter((c) => c.credential_id === currentCredentialId) ?? [])
        : [],
    [mcpConnections, currentCredentialId],
  )
  const sharedAcrossServers = linkedConnections.length > 1

  async function handleSave() {
    const credentialId = mode === CREDENTIAL_NONE ? null : mode
    try {
      // server PATCH와 connection PATCH는 독립 row → 병렬. 한쪽 실패 시 toast.saveFailed로
      // 사용자에게 fallthrough; 부분 적용 가능성은 M6 cleanup에서 트랜잭셔널 endpoint로 정리.
      const tasks: Array<Promise<unknown>> = [
        updateServer.mutateAsync({ id: mcpServerId, data: { credential_id: credentialId } }),
      ]
      // 단일 connection만 안전 sync. N:1 공유는 server PATCH만 + 다른 server 영향 없음.
      if (!sharedAcrossServers && linkedConnections[0]) {
        tasks.push(
          updateConnection.mutateAsync({
            id: linkedConnections[0].id,
            data: { credential_id: credentialId, status: 'active' },
          }),
        )
      }
      await Promise.all(tasks)
      toast.success(
        sharedAcrossServers ? t('toast.savedSharedCredential') : t('toast.saved'),
      )
      onBound?.({ serverId: mcpServerId, credentialId })
      onClose()
    } catch {
      toast.error(t('toast.saveFailed'))
    }
  }

  const isPending = updateServer.isPending || updateConnection.isPending

  return (
    <>
      <DialogHeader>
        <DialogTitle className="flex items-center gap-2">
          <ServerIcon className="size-4" />
          {t('mcp.title', { serverName: serverName ?? '' })}
        </DialogTitle>
        <DialogDescription>{t('mcp.description')}</DialogDescription>
      </DialogHeader>

      <div className="space-y-4 py-2">
        {sharedAcrossServers && (
          <div
            role="alert"
            className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 p-2.5 text-xs text-amber-800"
          >
            <AlertTriangleIcon className="mt-0.5 size-3.5 shrink-0" />
            <span>{t('mcp.sharedCredentialWarning')}</span>
          </div>
        )}

        <div className="space-y-2">
          <label className="text-sm font-medium flex items-center gap-1.5">
            <LinkIcon className="size-3.5" />
            {tCred('label')}
          </label>
          <CredentialSelect
            value={mode}
            onValueChange={setMode}
            onCreateRequested={() => setCreateOpen(true)}
            credentials={availableCredentials}
          />
        </div>

        {mode !== CREDENTIAL_NONE && (
          <div className="flex items-center gap-2 text-xs text-emerald-600">
            <CheckCircleIcon className="size-3.5" />
            {t('configured')}
          </div>
        )}
      </div>

      <DialogFooter>
        <Button variant="outline" onClick={onClose}>
          {tc('cancel')}
        </Button>
        <Button onClick={handleSave} disabled={isPending}>
          {isPending && (
            <Loader2Icon className="size-4 animate-spin" data-icon="inline-start" />
          )}
          {tc('save')}
        </Button>
      </DialogFooter>

      <CredentialFormDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={(c) => setMode(c.id)}
      />
    </>
  )
}
