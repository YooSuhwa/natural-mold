'use client'

import { useState } from 'react'
import { Loader2Icon, CheckCircleIcon, WrenchIcon } from 'lucide-react'
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
import type { Tool } from '@/lib/types'

interface AddToolDialogProps {
  trigger: React.ReactNode
}

export function AddToolDialog({ trigger }: AddToolDialogProps) {
  const t = useTranslations('tool.addDialog')
  const tc = useTranslations('common')
  const [open, setOpen] = useState(false)

  // MCP form state
  const [mcpName, setMcpName] = useState('')
  const [mcpUrl, setMcpUrl] = useState('')
  const [mcpAuthType, setMcpAuthType] = useState('none')
  const [mcpApiKey, setMcpApiKey] = useState('')
  const [discoveredTools, setDiscoveredTools] = useState<Tool[] | null>(null)
  const registerMCP = useRegisterMCPServer()

  // Custom tool form state
  const [customName, setCustomName] = useState('')
  const [customDescription, setCustomDescription] = useState('')
  const [customApiUrl, setCustomApiUrl] = useState('')
  const [customMethod, setCustomMethod] = useState('GET')
  const [customParams, setCustomParams] = useState('')
  const [customAuthType, setCustomAuthType] = useState('none')
  const [customApiKey, setCustomApiKey] = useState('')
  const createCustomTool = useCreateCustomTool()

  function resetForms() {
    setMcpName('')
    setMcpUrl('')
    setMcpAuthType('none')
    setMcpApiKey('')
    setDiscoveredTools(null)
    setCustomName('')
    setCustomDescription('')
    setCustomApiUrl('')
    setCustomMethod('GET')
    setCustomParams('')
    setCustomAuthType('none')
    setCustomApiKey('')
  }

  function handleClose() {
    resetForms()
    setOpen(false)
  }

  async function handleMCPSubmit() {
    const authConfig = mcpAuthType !== 'none' ? { api_key: mcpApiKey } : undefined
    const result = await registerMCP.mutateAsync({
      name: mcpName,
      url: mcpUrl,
      auth_type: mcpAuthType !== 'none' ? mcpAuthType : undefined,
      auth_config: authConfig,
    })
    setDiscoveredTools(result.tools)
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
    const authConfig = customAuthType !== 'none' ? { api_key: customApiKey } : undefined
    await createCustomTool.mutateAsync({
      name: customName,
      description: customDescription || undefined,
      api_url: customApiUrl,
      http_method: customMethod,
      parameters_schema: parsedParams,
      auth_type: customAuthType !== 'none' ? customAuthType : undefined,
      auth_config: authConfig,
    })
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
                  <div className="flex gap-4 text-sm">
                    {[
                      { value: 'none', label: t('auth.none') },
                      { value: 'api_key', label: t('auth.apiKey') },
                      { value: 'oauth', label: t('auth.oauth') },
                    ].map((opt) => (
                      <label key={opt.value} className="flex items-center gap-1.5">
                        <input
                          type="radio"
                          name="mcp-auth"
                          value={opt.value}
                          checked={mcpAuthType === opt.value}
                          onChange={(e) => setMcpAuthType(e.target.value)}
                        />
                        {opt.label}
                      </label>
                    ))}
                  </div>
                </div>
                {mcpAuthType !== 'none' && (
                  <div className="space-y-2">
                    <label className="text-sm font-medium">{t('auth.apiKey')}</label>
                    <Input
                      value={mcpApiKey}
                      onChange={(e) => setMcpApiKey(e.target.value)}
                      type="password"
                      placeholder={t('auth.apiKeyPlaceholder')}
                    />
                  </div>
                )}
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
                  <label className="text-sm font-medium">{t('custom.auth')}</label>
                  <div className="flex gap-4 text-sm">
                    {[
                      { value: 'none', label: t('auth.none') },
                      { value: 'api_key', label: t('auth.apiKey') },
                      { value: 'bearer', label: t('auth.bearer') },
                    ].map((opt) => (
                      <label key={opt.value} className="flex items-center gap-1.5">
                        <input
                          type="radio"
                          name="custom-auth"
                          value={opt.value}
                          checked={customAuthType === opt.value}
                          onChange={(e) => setCustomAuthType(e.target.value)}
                        />
                        {opt.label}
                      </label>
                    ))}
                  </div>
                </div>
                {customAuthType !== 'none' && (
                  <div className="space-y-2">
                    <label className="text-sm font-medium">{t('auth.apiKey')}</label>
                    <Input
                      value={customApiKey}
                      onChange={(e) => setCustomApiKey(e.target.value)}
                      type="password"
                      placeholder={t('auth.apiKeyPlaceholder')}
                    />
                  </div>
                )}
                <DialogFooter>
                  <Button
                    onClick={handleCustomSubmit}
                    disabled={
                      !customName.trim() || !customApiUrl.trim() || createCustomTool.isPending
                    }
                  >
                    {createCustomTool.isPending && (
                      <Loader2Icon className="mr-1 size-4 animate-spin" />
                    )}
                    {tc('register')}
                  </Button>
                </DialogFooter>
              </TabsContent>
            </Tabs>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
