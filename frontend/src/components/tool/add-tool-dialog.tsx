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
import {
  useConnections,
  useCreateConnection,
  useFindOrCreateCustomConnection,
} from '@/lib/hooks/use-connections'
import { CUSTOM_CONNECTION_PROVIDER_NAME } from '@/lib/types'
import type { Tool } from '@/lib/types'
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

  // CUSTOM connection 캐시 구독 — find-or-create 훅이 캐시에서 기존 row를 찾을
  // 수 있도록 이 컴포넌트에서 쿼리를 켜 둔다.
  useConnections({ type: 'custom', provider_name: CUSTOM_CONNECTION_PROVIDER_NAME })
  const createConnection = useCreateConnection()
  const findOrCreateCustomConnection = useFindOrCreateCustomConnection()
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
        // M6 이후 auth SOT = connection. tool.credential_id는 제거됨.
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
                  <Button
                    onClick={handleCustomSubmit}
                    disabled={
                      !customName.trim() ||
                      !customApiUrl.trim() ||
                      customCredentialId === CREDENTIAL_NONE ||
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
