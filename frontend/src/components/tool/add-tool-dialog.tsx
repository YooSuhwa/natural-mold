'use client'

import { useState } from 'react'
import { Loader2Icon } from 'lucide-react'
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
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { useRegisterMCPServer, useCreateCustomTool } from '@/lib/hooks/use-tools'

interface AddToolDialogProps {
  trigger: React.ReactNode
}

export function AddToolDialog({ trigger }: AddToolDialogProps) {
  const [open, setOpen] = useState(false)

  // MCP form state
  const [mcpName, setMcpName] = useState('')
  const [mcpUrl, setMcpUrl] = useState('')
  const [mcpAuthType, setMcpAuthType] = useState('none')
  const [mcpApiKey, setMcpApiKey] = useState('')
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
    setCustomName('')
    setCustomDescription('')
    setCustomApiUrl('')
    setCustomMethod('GET')
    setCustomParams('')
    setCustomAuthType('none')
    setCustomApiKey('')
  }

  async function handleMCPSubmit() {
    const authConfig = mcpAuthType !== 'none' ? { api_key: mcpApiKey } : undefined
    await registerMCP.mutateAsync({
      name: mcpName,
      url: mcpUrl,
      auth_type: mcpAuthType !== 'none' ? mcpAuthType : undefined,
      auth_config: authConfig,
    })
    resetForms()
    setOpen(false)
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
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={trigger as React.ReactElement} />
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>도구 추가</DialogTitle>
          <DialogDescription>
            MCP 서버를 등록하거나 커스텀 도구를 직접 정의하세요.
          </DialogDescription>
        </DialogHeader>

        <Tabs defaultValue="mcp">
          <TabsList className="w-full">
            <TabsTrigger value="mcp" className="flex-1">
              MCP 서버
            </TabsTrigger>
            <TabsTrigger value="custom" className="flex-1">
              직접 정의
            </TabsTrigger>
          </TabsList>

          {/* MCP Server Tab */}
          <TabsContent value="mcp" className="space-y-4 pt-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">서버 이름</label>
              <Input
                value={mcpName}
                onChange={(e) => setMcpName(e.target.value)}
                placeholder="Google Workspace MCP"
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">서버 URL</label>
              <Input
                value={mcpUrl}
                onChange={(e) => setMcpUrl(e.target.value)}
                placeholder="https://mcp.example.com"
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">인증</label>
              <div className="flex gap-4 text-sm">
                {[
                  { value: 'none', label: '없음' },
                  { value: 'api_key', label: 'API Key' },
                  { value: 'oauth', label: 'OAuth' },
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
                <label className="text-sm font-medium">API Key</label>
                <Input
                  value={mcpApiKey}
                  onChange={(e) => setMcpApiKey(e.target.value)}
                  type="password"
                  placeholder="sk-xxxxxxxxxxxx"
                />
              </div>
            )}
            <DialogFooter>
              <Button
                onClick={handleMCPSubmit}
                disabled={!mcpName.trim() || !mcpUrl.trim() || registerMCP.isPending}
              >
                {registerMCP.isPending && <Loader2Icon className="mr-1 size-4 animate-spin" />}
                등록
              </Button>
            </DialogFooter>
          </TabsContent>

          {/* Custom Tool Tab */}
          <TabsContent value="custom" className="space-y-4 pt-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">
                도구 이름 <span className="text-destructive">*</span>
              </label>
              <Input
                value={customName}
                onChange={(e) => setCustomName(e.target.value)}
                placeholder="날씨 조회"
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">설명</label>
              <Input
                value={customDescription}
                onChange={(e) => setCustomDescription(e.target.value)}
                placeholder="도시의 현재 날씨를 조회합니다"
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">
                API URL <span className="text-destructive">*</span>
              </label>
              <Input
                value={customApiUrl}
                onChange={(e) => setCustomApiUrl(e.target.value)}
                placeholder="https://api.example.com/weather"
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">HTTP 메서드</label>
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
              <label className="text-sm font-medium">파라미터 (JSON Schema)</label>
              <Textarea
                value={customParams}
                onChange={(e) => setCustomParams(e.target.value)}
                placeholder='{ "type": "object", "properties": { "city": { "type": "string" } } }'
                rows={4}
                className="font-mono text-xs"
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">인증</label>
              <div className="flex gap-4 text-sm">
                {[
                  { value: 'none', label: '없음' },
                  { value: 'api_key', label: 'API Key' },
                  { value: 'bearer', label: 'Bearer' },
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
                <label className="text-sm font-medium">API Key</label>
                <Input
                  value={customApiKey}
                  onChange={(e) => setCustomApiKey(e.target.value)}
                  type="password"
                  placeholder="sk-xxxxxxxxxxxx"
                />
              </div>
            )}
            <DialogFooter>
              <Button
                onClick={handleCustomSubmit}
                disabled={!customName.trim() || !customApiUrl.trim() || createCustomTool.isPending}
              >
                {createCustomTool.isPending && <Loader2Icon className="mr-1 size-4 animate-spin" />}
                등록
              </Button>
            </DialogFooter>
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}
