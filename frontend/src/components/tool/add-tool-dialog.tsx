'use client'

import { useState } from 'react'
import { Loader2Icon } from 'lucide-react'
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
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { useCreateCustomTool } from '@/lib/hooks/use-tools'
import { useCredentials } from '@/lib/hooks/use-credentials'
import {
  useConnections,
  useCreateConnection,
  useFindOrCreateCustomConnection,
} from '@/lib/hooks/use-connections'
import { CUSTOM_CONNECTION_PROVIDER_NAME } from '@/lib/types'
import { ApiError } from '@/lib/api/client'
import { CredentialFormDialog } from '@/components/tool/credential-form-dialog'
import { CredentialSelect, CREDENTIAL_NONE } from '@/components/tool/credential-select'

interface AddToolDialogProps {
  trigger: React.ReactNode
}

// M6.1 M5 — MCP 신규 등록 탭 제거. backend `/mcp-server/*` 경로는 Jensen M3에서
// drop. 기존 MCP tool의 credential rotate는 그대로 (mcp-server-group-card →
// ConnectionBindingDialog type="mcp"). MCP 서버 신규 등록 UI는 후속 작업.
export function AddToolDialog({ trigger }: AddToolDialogProps) {
  const t = useTranslations('tool.addDialog')
  const tc = useTranslations('common')
  const [open, setOpen] = useState(false)
  const { data: credentials } = useCredentials()

  const [customName, setCustomName] = useState('')
  const [customDescription, setCustomDescription] = useState('')
  const [customApiUrl, setCustomApiUrl] = useState('')
  const [customMethod, setCustomMethod] = useState('GET')
  const [customParams, setCustomParams] = useState('')
  const [customCredentialId, setCustomCredentialId] = useState<string>(CREDENTIAL_NONE)
  const [credentialDialogOpen, setCredentialDialogOpen] = useState(false)
  const createCustomTool = useCreateCustomTool()

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
        <DialogHeader>
          <DialogTitle>{t('title')}</DialogTitle>
          <DialogDescription>{t('description')}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4 pt-2">
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
              onCreateRequested={() => setCredentialDialogOpen(true)}
              credentials={availableCredentials}
            />
            {customCredentialId !== CREDENTIAL_NONE ? (
              <p className="text-xs text-muted-foreground">{t('custom.connectionHint')}</p>
            ) : (
              <p className="text-xs text-amber-700">{t('custom.credentialRequired')}</p>
            )}
          </div>
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

        <CredentialFormDialog
          open={credentialDialogOpen}
          onOpenChange={setCredentialDialogOpen}
          onCreated={(c) => setCustomCredentialId(c.id)}
        />
      </DialogContent>
    </Dialog>
  )
}
