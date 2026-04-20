'use client'

import { useState } from 'react'
import { Loader2Icon, CheckCircleIcon, WrenchIcon } from 'lucide-react'
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
import { useTranslations } from 'next-intl'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { useRegisterMCPServer, useCreateCustomTool } from '@/lib/hooks/use-tools'
import { useCredentials } from '@/lib/hooks/use-credentials'
import { scopeKey, useConnections, useCreateConnection } from '@/lib/hooks/use-connections'
import { CUSTOM_CONNECTION_PROVIDER_NAME as CUSTOM_PROVIDER_NAME } from '@/lib/types'
import type { Connection, Tool } from '@/lib/types'
import { ApiError } from '@/lib/api/client'
import { CredentialFormDialog } from '@/components/tool/credential-form-dialog'
import { CredentialSelect, CREDENTIAL_NONE } from '@/components/tool/credential-select'

interface AddToolDialogProps {
  trigger: React.ReactNode
}

export function AddToolDialog({ trigger }: AddToolDialogProps) {
  const t = useTranslations('tool.addDialog')
  const tc = useTranslations('common')
  const [open, setOpen] = useState(false)
  const { data: credentials } = useCredentials()

  // MCP form state
  const [mcpName, setMcpName] = useState('')
  const [mcpUrl, setMcpUrl] = useState('')
  const [mcpCredentialId, setMcpMode] = useState<string>(CREDENTIAL_NONE)
  const [credentialDialogOpen, setCredentialDialogOpen] = useState(false)
  const [credentialTarget, setCredentialTarget] = useState<'mcp' | 'custom'>('mcp')
  const [discoveredTools, setDiscoveredTools] = useState<Tool[] | null>(null)
  const registerMCP = useRegisterMCPServer()

  // Custom tool form state
  const [customName, setCustomName] = useState('')
  const [customDescription, setCustomDescription] = useState('')
  const [customApiUrl, setCustomApiUrl] = useState('')
  const [customMethod, setCustomMethod] = useState('GET')
  const [customParams, setCustomParams] = useState('')
  const [customCredentialId, setCustomCredentialId] = useState<string>(CREDENTIAL_NONE)
  const createCustomTool = useCreateCustomTool()

  // CUSTOM connection find-or-create (M4): `useConnections` 구독으로 캐시를
  // 채워 두면 `resolveCustomConnectionId`가 `qc.getQueryData`로 최신 상태를
  // 직접 읽는다. tool POST는 find-or-create 후 connection_id만 전달한다.
  useConnections({ type: 'custom', provider_name: CUSTOM_PROVIDER_NAME })
  const createConnection = useCreateConnection()
  const qc = useQueryClient()

  const availableCredentials = credentials ?? []

  function resetForms() {
    setMcpName('')
    setMcpUrl('')
    setMcpMode(CREDENTIAL_NONE)
    setDiscoveredTools(null)
    setCustomName('')
    setCustomDescription('')
    setCustomApiUrl('')
    setCustomMethod('GET')
    setCustomParams('')
    setCustomCredentialId(CREDENTIAL_NONE)
  }

  function handleClose() {
    resetForms()
    setOpen(false)
  }

  async function handleMCPSubmit() {
    const payload =
      mcpCredentialId === CREDENTIAL_NONE
        ? { name: mcpName, url: mcpUrl, auth_type: 'none' as const }
        : { name: mcpName, url: mcpUrl, credential_id: mcpCredentialId }
    const result = await registerMCP.mutateAsync(payload)
    setDiscoveredTools(result.tools)
  }

  async function resolveCustomConnectionId(credentialId: string): Promise<string> {
    // 같은 credential을 공유하는 N tools는 1 connection을 재사용한다 (ADR-008 N:1).
    // 직전 onSuccess가 갱신한 최신 cache를 직접 read — closure snapshot은
    // render 시점 값이라 stale.
    const cached = qc.getQueryData<Connection[]>(
      scopeKey({ type: 'custom', provider_name: CUSTOM_PROVIDER_NAME }),
    )
    const existing = cached?.find((c) => c.credential_id === credentialId)
    if (existing) return existing.id

    const credential = availableCredentials.find((c) => c.id === credentialId)
    // is_default는 서버 판단에 위임 — 기존 CUSTOM default가 있으면 partial unique
    // index(`uq_connections_one_default_per_scope`) 때문에 강제 true 전송 시 409.
    const created = await createConnection.mutateAsync({
      type: 'custom',
      provider_name: CUSTOM_PROVIDER_NAME,
      display_name: credential?.name ?? customName,
      credential_id: credentialId,
    })
    return created.id
  }

  async function handleCustomSubmit() {
    let parsedParams: Record<string, unknown> | undefined
    if (customParams.trim()) {
      try {
        parsedParams = JSON.parse(customParams)
      } catch {
        return // Invalid JSON
      }
    }

    let connectionId: string | undefined
    if (customCredentialId !== CREDENTIAL_NONE) {
      try {
        connectionId = await resolveCustomConnectionId(customCredentialId)
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          qc.invalidateQueries({
            queryKey: ['connections', 'custom', CUSTOM_PROVIDER_NAME],
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
        // Bridge 기간(M5까지): tools/page.tsx의 getAuthStatus와 custom-auth-dialog가
        // 아직 `tool.credential_id`로 "configured" 여부를 판정하므로, 신규 row도
        // credential_id를 함께 전송해 UX 회귀를 막는다 (Codex P1). m11 dedup은
        // `connection_id IS NULL` row만 노리므로 둘 다 채워도 충돌 없음. M5에서
        // 위 consumer를 connection 기반으로 전환한 뒤 이 필드를 제거.
        ...(customCredentialId !== CREDENTIAL_NONE
          ? { credential_id: customCredentialId }
          : {}),
        ...(connectionId ? { connection_id: connectionId } : {}),
      })
    } catch {
      toast.error(t('custom.toolFailed'))
      return
    }
    resetForms()
    setOpen(false)
  }

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
        {discoveredTools !== null ? (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <CheckCircleIcon className="size-4 text-emerald-600" />
                {t('mcp.registrationComplete')}
              </DialogTitle>
              <DialogDescription>
                {discoveredTools.length > 0
                  ? t('mcp.discoveredTools', { count: discoveredTools.length })
                  : t('mcp.noToolsFound')}
              </DialogDescription>
            </DialogHeader>

            {discoveredTools.length > 0 && (
              <ul className="max-h-60 space-y-2 overflow-auto py-2">
                {discoveredTools.map((tool) => (
                  <li key={tool.id} className="flex items-start gap-2 rounded-md border p-2.5">
                    <WrenchIcon className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
                    <div className="min-w-0">
                      <p className="text-sm font-medium">{tool.name}</p>
                      {tool.description && (
                        <p className="text-xs text-muted-foreground line-clamp-2">
                          {tool.description}
                        </p>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}

            <DialogFooter>
              <Button onClick={handleClose}>{tc('confirm')}</Button>
            </DialogFooter>
          </>
        ) : (
          <>
            <DialogHeader>
              <DialogTitle>{t('title')}</DialogTitle>
              <DialogDescription>{t('description')}</DialogDescription>
            </DialogHeader>

            <Tabs defaultValue="mcp">
              <TabsList className="w-full">
                <TabsTrigger value="mcp" className="flex-1">
                  {t('tab.mcp')}
                </TabsTrigger>
                <TabsTrigger value="custom" className="flex-1">
                  {t('tab.custom')}
                </TabsTrigger>
              </TabsList>

              {/* MCP Server Tab */}
              <TabsContent value="mcp" className="space-y-4 pt-4">
                <div className="space-y-2">
                  <label className="text-sm font-medium">{t('mcp.serverName')}</label>
                  <Input
                    value={mcpName}
                    onChange={(e) => setMcpName(e.target.value)}
                    placeholder={t('mcp.serverNamePlaceholder')}
                  />
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium">{t('mcp.serverUrl')}</label>
                  <Input
                    value={mcpUrl}
                    onChange={(e) => setMcpUrl(e.target.value)}
                    placeholder={t('mcp.serverUrlPlaceholder')}
                  />
                </div>

                <div className="space-y-2">
                  <label className="text-sm font-medium">{t('auth.label')}</label>
                  <CredentialSelect
                    value={mcpCredentialId}
                    onValueChange={setMcpMode}
                    onCreateRequested={() => {
                      setCredentialTarget('mcp')
                      setCredentialDialogOpen(true)
                    }}
                    credentials={availableCredentials}
                  />
                </div>

                <DialogFooter>
                  <Button
                    onClick={handleMCPSubmit}
                    disabled={!mcpName.trim() || !mcpUrl.trim() || registerMCP.isPending}
                  >
                    {registerMCP.isPending && <Loader2Icon className="mr-1 size-4 animate-spin" />}
                    {tc('register')}
                  </Button>
                </DialogFooter>
              </TabsContent>

              {/* Custom Tool Tab */}
              <TabsContent value="custom" className="space-y-4 pt-4">
                <div className="space-y-2">
                  <label className="text-sm font-medium">
                    {t('custom.name')} <span className="text-destructive">{tc('required')}</span>
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
                    {t('custom.apiUrl')} <span className="text-destructive">{tc('required')}</span>
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
                    onCreateRequested={() => {
                      setCredentialTarget('custom')
                      setCredentialDialogOpen(true)
                    }}
                    credentials={availableCredentials}
                  />
                  {customCredentialId !== CREDENTIAL_NONE && (
                    <p className="text-xs text-muted-foreground">
                      {t('custom.connectionHint')}
                    </p>
                  )}
                </div>
                <DialogFooter>
                  <Button
                    onClick={handleCustomSubmit}
                    disabled={
                      !customName.trim() ||
                      !customApiUrl.trim() ||
                      createCustomTool.isPending ||
                      createConnection.isPending
                    }
                  >
                    {(createCustomTool.isPending || createConnection.isPending) && (
                      <Loader2Icon className="mr-1 size-4 animate-spin" />
                    )}
                    {tc('register')}
                  </Button>
                </DialogFooter>
              </TabsContent>
            </Tabs>
          </>
        )}

        <CredentialFormDialog
          open={credentialDialogOpen}
          onOpenChange={setCredentialDialogOpen}
          onCreated={(c) => {
            if (credentialTarget === 'mcp') setMcpMode(c.id)
            else setCustomCredentialId(c.id)
          }}
        />
      </DialogContent>
    </Dialog>
  )
}
