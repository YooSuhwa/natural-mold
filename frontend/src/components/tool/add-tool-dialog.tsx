'use client'

import { useState } from 'react'
import { Loader2Icon, ServerIcon, WrenchIcon } from 'lucide-react'
import { toast } from 'sonner'
import { useQueryClient } from '@tanstack/react-query'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { useCreateCustomTool } from '@/lib/hooks/use-tools'
import { useCredentials } from '@/lib/hooks/use-credentials'
import {
  useConnections,
  useCreateConnection,
  useDiscoverMcpTools,
  useFindOrCreateCustomConnection,
} from '@/lib/hooks/use-connections'
import { CUSTOM_CONNECTION_PROVIDER_NAME } from '@/lib/types'
import { ApiError } from '@/lib/api/client'
import { CredentialFormDialog } from '@/components/tool/credential-form-dialog'
import { CredentialSelect, CREDENTIAL_NONE } from '@/components/tool/credential-select'

interface AddToolDialogProps {
  trigger: React.ReactNode
}

// 표시 이름 → provider_name 슬러그. 백엔드 validator는 ^[a-z0-9_]+$ 강제 (길이 ≤50).
// 한글/이모지 등 ASCII 외 입력은 빈 normalized → random suffix로 scope 내 중복 회피.
function slugify(raw: string): string {
  const normalized = raw
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 50)
  if (normalized) return normalized
  return `mcp_${Math.random().toString(36).slice(2, 8)}`
}

export function AddToolDialog({ trigger }: AddToolDialogProps) {
  const t = useTranslations('tool.addDialog')
  const tc = useTranslations('common')
  const [open, setOpen] = useState(false)
  const { data: credentials } = useCredentials()

  // CUSTOM form state
  const [customName, setCustomName] = useState('')
  const [customDescription, setCustomDescription] = useState('')
  const [customApiUrl, setCustomApiUrl] = useState('')
  const [customMethod, setCustomMethod] = useState('GET')
  const [customParams, setCustomParams] = useState('')
  const [customCredentialId, setCustomCredentialId] = useState<string>(CREDENTIAL_NONE)
  const [credentialDialogOpen, setCredentialDialogOpen] = useState(false)
  const createCustomTool = useCreateCustomTool()

  // MCP form state
  const [mcpDisplayName, setMcpDisplayName] = useState('')
  const [mcpUrl, setMcpUrl] = useState('')
  const [mcpAuthEnabled, setMcpAuthEnabled] = useState(false)
  const [mcpHeaderName, setMcpHeaderName] = useState('Authorization')
  const [mcpCredentialId, setMcpCredentialId] = useState<string>(CREDENTIAL_NONE)
  const [mcpCredentialField, setMcpCredentialField] = useState<string>('')
  const discoverMcpTools = useDiscoverMcpTools()

  // CUSTOM connection 캐시 구독 — find-or-create 훅이 캐시에서 기존 row를 찾을
  // 수 있도록 이 컴포넌트에서 쿼리를 켜 둔다.
  useConnections({ type: 'custom', provider_name: CUSTOM_CONNECTION_PROVIDER_NAME })
  const createConnection = useCreateConnection()
  const findOrCreateCustomConnection = useFindOrCreateCustomConnection()
  const qc = useQueryClient()

  const availableCredentials = credentials ?? []

  function resetForms() {
    setCustomName('')
    setCustomDescription('')
    setCustomApiUrl('')
    setCustomMethod('GET')
    setCustomParams('')
    setCustomCredentialId(CREDENTIAL_NONE)
    setMcpDisplayName('')
    setMcpUrl('')
    setMcpAuthEnabled(false)
    setMcpHeaderName('Authorization')
    setMcpCredentialId(CREDENTIAL_NONE)
    setMcpCredentialField('')
  }

  function handleClose() {
    resetForms()
    setOpen(false)
  }

  async function resolveCustomConnectionId(credentialId: string): Promise<string> {
    const credential = availableCredentials.find((c) => c.id === credentialId)
    const conn = await findOrCreateCustomConnection.run(
      credentialId,
      credential?.name ?? customName,
    )
    return conn.id
  }

  async function handleCustomSubmit() {
    let parsedParams: Record<string, unknown> | undefined
    if (customParams.trim()) {
      try {
        parsedParams = JSON.parse(customParams)
      } catch {
        return
      }
    }

    let connectionId: string | undefined
    if (customCredentialId !== CREDENTIAL_NONE) {
      try {
        connectionId = await resolveCustomConnectionId(customCredentialId)
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          qc.invalidateQueries({
            queryKey: ['connections', 'custom', CUSTOM_CONNECTION_PROVIDER_NAME],
          })
          qc.invalidateQueries({ queryKey: ['connections'] })
          toast.error(t('custom.connectionConflict'))
          return
        }
        toast.error(t('custom.connectionFailed'))
        return
      }
    }

    try {
      await createCustomTool.mutateAsync({
        name: customName,
        description: customDescription || undefined,
        api_url: customApiUrl,
        http_method: customMethod,
        parameters_schema: parsedParams,
        ...(connectionId ? { connection_id: connectionId } : {}),
      })
    } catch {
      toast.error(t('custom.toolFailed'))
      return
    }
    handleClose()
  }

  async function handleMcpSubmit() {
    const trimmedName = mcpDisplayName.trim()
    const trimmedUrl = mcpUrl.trim()
    if (!trimmedName || !trimmedUrl) return

    // 인증 사용 시: api_key auth_type + env_vars 템플릿 매핑.
    // backend env_var_resolver는 전체 매칭만 지원 — 부분 치환 불가하므로
    // credential 값에 prefix(예: "Bearer ")가 필요하면 사용자가 credential
    // 저장 시 prefix를 포함해 저장해야 한다.
    const useAuth =
      mcpAuthEnabled &&
      mcpCredentialId !== CREDENTIAL_NONE &&
      mcpHeaderName.trim() &&
      mcpCredentialField.trim()
    const extraConfig = useAuth
      ? {
          url: trimmedUrl,
          auth_type: 'api_key' as const,
          env_vars: {
            [mcpHeaderName.trim()]: `\${credential.${mcpCredentialField.trim()}}`,
          },
        }
      : { url: trimmedUrl, auth_type: 'none' as const }

    let connectionId: string
    try {
      const created = await createConnection.mutateAsync({
        type: 'mcp',
        provider_name: slugify(trimmedName),
        display_name: trimmedName,
        credential_id: useAuth ? mcpCredentialId : null,
        extra_config: extraConfig,
      })
      connectionId = created.id
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : String(err)
      toast.error(t('mcp.connectionFailed', { detail }))
      return
    }

    try {
      const result = await discoverMcpTools.mutateAsync(connectionId)
      const created = result.items.filter((i) => i.status === 'created').length
      const existing = result.items.filter((i) => i.status === 'existing').length
      toast.success(t('mcp.discoverSuccess', { created, existing }))
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : String(err)
      toast.error(t('mcp.discoverFailed', { detail }))
      // connection은 이미 생성됨 — Connection 상세에서 재탐색 가능 (rediscover 버튼).
    }

    handleClose()
  }

  const customSubmitDisabled =
    !customName.trim() ||
    !customApiUrl.trim() ||
    customCredentialId === CREDENTIAL_NONE ||
    createCustomTool.isPending ||
    createConnection.isPending
  const selectedMcpCredential = availableCredentials.find((c) => c.id === mcpCredentialId)
  const mcpAuthIncomplete =
    mcpAuthEnabled &&
    (mcpCredentialId === CREDENTIAL_NONE ||
      !mcpHeaderName.trim() ||
      !mcpCredentialField.trim())
  const mcpSubmitDisabled =
    !mcpDisplayName.trim() ||
    !mcpUrl.trim() ||
    mcpAuthIncomplete ||
    createConnection.isPending ||
    discoverMcpTools.isPending
  const customPending = createCustomTool.isPending || createConnection.isPending
  const mcpPending = createConnection.isPending || discoverMcpTools.isPending

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) handleClose()
        else setOpen(true)
      }}
    >
      <DialogTrigger render={trigger as React.ReactElement} />
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t('title')}</DialogTitle>
          <DialogDescription>{t('description')}</DialogDescription>
        </DialogHeader>

        <Tabs defaultValue="mcp" className="pt-2">
          <TabsList className="grid grid-cols-2 w-full">
            <TabsTrigger value="mcp">
              <ServerIcon className="size-3.5" data-icon="inline-start" />
              {t('tab.mcp')}
            </TabsTrigger>
            <TabsTrigger value="custom">
              <WrenchIcon className="size-3.5" data-icon="inline-start" />
              {t('tab.custom')}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="mcp" className="space-y-4 pt-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">
                {t('mcp.serverName')}{' '}
                <span className="text-destructive">{tc('required')}</span>
              </label>
              <Input
                value={mcpDisplayName}
                onChange={(e) => setMcpDisplayName(e.target.value)}
                placeholder={t('mcp.serverNamePlaceholder')}
                maxLength={200}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">
                {t('mcp.serverUrl')}{' '}
                <span className="text-destructive">{tc('required')}</span>
              </label>
              <Input
                value={mcpUrl}
                onChange={(e) => setMcpUrl(e.target.value)}
                placeholder={t('mcp.serverUrlPlaceholder')}
                type="url"
                maxLength={500}
              />
            </div>

            <div className="space-y-3 rounded-md border p-3">
              <label className="flex items-center gap-2 text-sm font-medium cursor-pointer">
                <input
                  type="checkbox"
                  checked={mcpAuthEnabled}
                  onChange={(e) => setMcpAuthEnabled(e.target.checked)}
                />
                {t('mcp.authEnabled')}
              </label>
              {mcpAuthEnabled && (
                <div className="space-y-3 pt-1">
                  <div className="space-y-2">
                    <label className="text-xs font-medium text-muted-foreground">
                      {t('auth.label')}
                    </label>
                    <CredentialSelect
                      value={mcpCredentialId}
                      onValueChange={(v) => {
                        setMcpCredentialId(v)
                        setMcpCredentialField('')
                      }}
                      onCreateRequested={() => setCredentialDialogOpen(true)}
                      credentials={availableCredentials}
                    />
                  </div>
                  {selectedMcpCredential && (
                    <div className="space-y-2">
                      <label className="text-xs font-medium text-muted-foreground">
                        {t('mcp.credentialField')}
                      </label>
                      <select
                        className="w-full rounded-md border bg-background px-3 py-1.5 text-sm"
                        value={mcpCredentialField}
                        onChange={(e) => setMcpCredentialField(e.target.value)}
                      >
                        <option value="">{t('mcp.credentialFieldPlaceholder')}</option>
                        {selectedMcpCredential.field_keys.map((field) => (
                          <option key={field} value={field}>
                            {field}
                          </option>
                        ))}
                      </select>
                    </div>
                  )}
                  <div className="space-y-2">
                    <label className="text-xs font-medium text-muted-foreground">
                      {t('mcp.headerName')}
                    </label>
                    <Input
                      value={mcpHeaderName}
                      onChange={(e) => setMcpHeaderName(e.target.value)}
                      placeholder="Authorization"
                      maxLength={100}
                    />
                  </div>
                  <p className="text-xs text-muted-foreground">{t('mcp.authHint')}</p>
                </div>
              )}
              {!mcpAuthEnabled && (
                <p className="text-xs text-muted-foreground">{t('mcp.authNoticeV1')}</p>
              )}
            </div>

            <DialogFooter>
              <Button onClick={handleMcpSubmit} disabled={mcpSubmitDisabled}>
                {mcpPending && (
                  <Loader2Icon
                    className="size-4 animate-spin"
                    data-icon="inline-start"
                  />
                )}
                {t('mcp.submit')}
              </Button>
            </DialogFooter>
          </TabsContent>

          <TabsContent value="custom" className="space-y-4 pt-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">
                {t('custom.name')}{' '}
                <span className="text-destructive">{tc('required')}</span>
              </label>
              <Input
                value={customName}
                onChange={(e) => setCustomName(e.target.value)}
                placeholder={t('custom.namePlaceholder')}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">{t('custom.description')}</label>
              <Input
                value={customDescription}
                onChange={(e) => setCustomDescription(e.target.value)}
                placeholder={t('custom.descriptionPlaceholder')}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">
                {t('custom.apiUrl')}{' '}
                <span className="text-destructive">{tc('required')}</span>
              </label>
              <Input
                value={customApiUrl}
                onChange={(e) => setCustomApiUrl(e.target.value)}
                placeholder={t('custom.apiUrlPlaceholder')}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">{t('custom.httpMethod')}</label>
              <div className="flex gap-4 text-sm">
                {['GET', 'POST', 'PUT'].map((m) => (
                  <label key={m} className="flex items-center gap-1.5">
                    <input
                      type="radio"
                      name="custom-method"
                      value={m}
                      checked={customMethod === m}
                      onChange={(e) => setCustomMethod(e.target.value)}
                    />
                    {m}
                  </label>
                ))}
              </div>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">{t('custom.params')}</label>
              <Textarea
                value={customParams}
                onChange={(e) => setCustomParams(e.target.value)}
                placeholder='{ "type": "object", "properties": { "city": { "type": "string" } } }'
                rows={4}
                className="font-mono text-xs"
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">{t('auth.label')}</label>
              <CredentialSelect
                value={customCredentialId}
                onValueChange={setCustomCredentialId}
                onCreateRequested={() => setCredentialDialogOpen(true)}
                credentials={availableCredentials}
              />
              {customCredentialId !== CREDENTIAL_NONE ? (
                <p className="text-xs text-muted-foreground">
                  {t('custom.connectionHint')}
                </p>
              ) : (
                <p className="text-xs text-amber-700">
                  {t('custom.credentialRequired')}
                </p>
              )}
            </div>

            <DialogFooter>
              <Button onClick={handleCustomSubmit} disabled={customSubmitDisabled}>
                {customPending && (
                  <Loader2Icon
                    className="size-4 animate-spin"
                    data-icon="inline-start"
                  />
                )}
                {tc('register')}
              </Button>
            </DialogFooter>
          </TabsContent>
        </Tabs>

        <CredentialFormDialog
          open={credentialDialogOpen}
          onOpenChange={setCredentialDialogOpen}
          onCreated={(c) => setCustomCredentialId(c.id)}
        />
      </DialogContent>
    </Dialog>
  )
}
