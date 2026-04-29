'use client'

import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { ArrowLeft, ArrowRight, Loader2, Check } from 'lucide-react'

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { CredentialPicker } from '@/components/credential/credential-picker'
import { McpToolTable } from './mcp-tool-table'
import {
  useCreateMcpServer,
  useDiscoverMcpTools,
} from '@/lib/hooks/use-mcp-servers'
import type { McpTool, McpTransport } from '@/lib/types/mcp'

interface McpServerWizardProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

const STEPS = ['Basics', 'Auth', 'Discover', 'Confirm'] as const
type Step = (typeof STEPS)[number]

export function McpServerWizard({ open, onOpenChange }: McpServerWizardProps) {
  const [step, setStep] = useState<Step>('Basics')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [transport, setTransport] = useState<McpTransport>('streamable_http')
  const [url, setUrl] = useState('')
  const [command, setCommand] = useState('')
  const [credentialId, setCredentialId] = useState<string | null>(null)
  const [discoveredTools, setDiscoveredTools] = useState<McpTool[]>([])
  const [createdServerId, setCreatedServerId] = useState<string | null>(null)

  const create = useCreateMcpServer()
  const discover = useDiscoverMcpTools()

  function reset() {
    setStep('Basics')
    setName('')
    setDescription('')
    setTransport('streamable_http')
    setUrl('')
    setCommand('')
    setCredentialId(null)
    setDiscoveredTools([])
    setCreatedServerId(null)
  }

  function handleClose(next: boolean) {
    if (!next) reset()
    onOpenChange(next)
  }

  const stepIndex = STEPS.indexOf(step)

  const basicsValid = useMemo(() => {
    if (!name.trim()) return false
    if (transport === 'stdio') return command.trim().length > 0
    return url.trim().length > 0
  }, [name, transport, url, command])

  async function handleNext() {
    if (step === 'Basics') {
      if (!basicsValid) {
        toast.error('Fill the required fields')
        return
      }
      setStep('Auth')
      return
    }
    if (step === 'Auth') {
      // Create the server then move to discovery
      try {
        const server = await create.mutateAsync({
          name: name.trim(),
          description: description.trim() || null,
          transport,
          url: transport === 'stdio' ? null : url.trim(),
          command: transport === 'stdio' ? command.trim() : null,
          credential_id: credentialId,
        })
        setCreatedServerId(server.id)
        setStep('Discover')
      } catch (e) {
        toast.error(e instanceof Error ? e.message : 'Create failed')
      }
      return
    }
    if (step === 'Discover') {
      if (!createdServerId) return
      try {
        const result = await discover.mutateAsync(createdServerId)
        if (!result.success) {
          toast.error(result.error ?? 'Discovery failed')
          return
        }
        setDiscoveredTools(result.tools)
        setStep('Confirm')
      } catch (e) {
        toast.error(e instanceof Error ? e.message : 'Discovery failed')
      }
      return
    }
    if (step === 'Confirm') {
      toast.success('MCP server ready')
      handleClose(false)
    }
  }

  function handleBack() {
    if (step === 'Auth') setStep('Basics')
    else if (step === 'Discover') setStep('Auth')
    else if (step === 'Confirm') setStep('Discover')
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>New MCP server</DialogTitle>
          <DialogDescription>
            Step {stepIndex + 1} of {STEPS.length} — {step}
          </DialogDescription>
        </DialogHeader>

        {/* Stepper */}
        <ol className="flex items-center gap-2 text-xs">
          {STEPS.map((s, i) => (
            <li key={s} className="flex items-center gap-2">
              <span
                className={`flex size-5 items-center justify-center rounded-full ${
                  i < stepIndex
                    ? 'bg-emerald-500 text-white'
                    : i === stepIndex
                      ? 'bg-foreground text-background'
                      : 'bg-muted text-muted-foreground'
                }`}
              >
                {i < stepIndex ? <Check className="size-3" /> : i + 1}
              </span>
              <span
                className={
                  i === stepIndex
                    ? 'font-medium'
                    : 'text-muted-foreground'
                }
              >
                {s}
              </span>
              {i < STEPS.length - 1 && (
                <span className="text-muted-foreground">→</span>
              )}
            </li>
          ))}
        </ol>

        <Separator />

        <div className="min-h-[280px]">
          {step === 'Basics' && (
            <div className="space-y-3">
              <div className="space-y-1.5">
                <label htmlFor="mcp-name" className="text-xs font-medium">
                  Name <span className="text-destructive">*</span>
                </label>
                <Input
                  id="mcp-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <label htmlFor="mcp-desc" className="text-xs font-medium">
                  Description
                </label>
                <Textarea
                  id="mcp-desc"
                  value={description}
                  rows={2}
                  onChange={(e) => setDescription(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium">Transport</label>
                <Select
                  value={transport}
                  onValueChange={(v) => setTransport(v as McpTransport)}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="streamable_http">
                      Streamable HTTP
                    </SelectItem>
                    <SelectItem value="sse">SSE</SelectItem>
                    <SelectItem value="stdio">stdio</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {transport === 'stdio' ? (
                <div className="space-y-1.5">
                  <label htmlFor="mcp-command" className="text-xs font-medium">
                    Command <span className="text-destructive">*</span>
                  </label>
                  <Input
                    id="mcp-command"
                    value={command}
                    placeholder="/usr/local/bin/my-mcp-server"
                    onChange={(e) => setCommand(e.target.value)}
                  />
                </div>
              ) : (
                <div className="space-y-1.5">
                  <label htmlFor="mcp-url" className="text-xs font-medium">
                    URL <span className="text-destructive">*</span>
                  </label>
                  <Input
                    id="mcp-url"
                    value={url}
                    placeholder="https://example.com/mcp"
                    onChange={(e) => setUrl(e.target.value)}
                  />
                </div>
              )}
            </div>
          )}

          {step === 'Auth' && (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                Optionally bind a credential. The MCP client will resolve{' '}
                <code className="rounded bg-muted px-1">{'${credential.<field>}'}</code>{' '}
                templates in headers and env vars.
              </p>
              <CredentialPicker value={credentialId} onChange={setCredentialId} />
            </div>
          )}

          {step === 'Discover' && (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                We&apos;ll connect to the server and import its tools. Click{' '}
                <em>Next</em> to start.
              </p>
              {discover.isPending && (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" /> Discovering tools...
                </div>
              )}
            </div>
          )}

          {step === 'Confirm' && (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                {discoveredTools.length} tool{discoveredTools.length === 1 ? '' : 's'}{' '}
                imported.
              </p>
              <McpToolTable tools={discoveredTools} />
            </div>
          )}
        </div>

        <DialogFooter>
          {step !== 'Basics' && step !== 'Confirm' && (
            <Button variant="outline" onClick={handleBack}>
              <ArrowLeft className="size-4" /> Back
            </Button>
          )}
          <Button
            onClick={handleNext}
            disabled={
              (step === 'Basics' && !basicsValid) ||
              create.isPending ||
              discover.isPending
            }
          >
            {(create.isPending || discover.isPending) && (
              <Loader2 className="size-4 animate-spin" />
            )}
            {step === 'Confirm' ? 'Done' : 'Next'}
            {step !== 'Confirm' && <ArrowRight className="size-4" />}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
