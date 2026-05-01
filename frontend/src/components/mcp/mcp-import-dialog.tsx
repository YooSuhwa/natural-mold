'use client'

import { useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import { Download, FileJson, Upload } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Textarea } from '@/components/ui/textarea'
import { DialogShell } from '@/components/shared/dialog-shell'
import { FormFooter } from '@/components/shared/form-footer'
import { useImportMcpServers } from '@/lib/hooks/use-mcp-servers'
import type {
  McpImportRequest,
  McpImportResult,
} from '@/lib/types/mcp'

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  onImported?: (result: McpImportResult) => void
}

interface ParseOk {
  ok: true
  payload: McpImportRequest
  names: string[]
}

interface ParseErr {
  ok: false
  message: string
}

type ParseState = ParseOk | ParseErr | null

/** Validate the JSON shape minimally — backend re-validates and returns
 *  per-entry errors so we don't try to reproduce the full schema here. */
function parseImportJson(raw: string, overwrite: boolean): ParseState {
  const trimmed = raw.trim()
  if (!trimmed) return null
  let value: unknown
  try {
    value = JSON.parse(trimmed)
  } catch (e) {
    return {
      ok: false,
      message: e instanceof Error ? e.message : 'Invalid JSON',
    }
  }
  if (
    !value ||
    typeof value !== 'object' ||
    !('mcpServers' in (value as Record<string, unknown>))
  ) {
    return { ok: false, message: 'Missing top-level "mcpServers" object' }
  }
  const servers = (value as { mcpServers: unknown }).mcpServers
  if (!servers || typeof servers !== 'object' || Array.isArray(servers)) {
    return { ok: false, message: '"mcpServers" must be an object map' }
  }
  const names = Object.keys(servers as Record<string, unknown>)
  if (names.length === 0) {
    return { ok: false, message: '"mcpServers" is empty' }
  }
  return {
    ok: true,
    payload: {
      mcpServers: servers as McpImportRequest['mcpServers'],
      overwrite,
    },
    names,
  }
}

export function McpImportDialog({ open, onOpenChange, onImported }: Props) {
  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="md" height="fixed">
      {open ? <McpImportBody onClose={() => onOpenChange(false)} onImported={onImported} /> : null}
    </DialogShell>
  )
}

function McpImportBody({
  onClose,
  onImported,
}: {
  onClose: () => void
  onImported?: (result: McpImportResult) => void
}) {
  const importMutation = useImportMcpServers()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [raw, setRaw] = useState('')
  const [overwrite, setOverwrite] = useState(false)
  const [result, setResult] = useState<McpImportResult | null>(null)

  const parsed = useMemo(() => parseImportJson(raw, overwrite), [raw, overwrite])
  const canImport = parsed?.ok === true && !importMutation.isPending

  function handlePickFile() {
    fileInputRef.current?.click()
  }

  async function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    try {
      const text = await file.text()
      setRaw(text)
      setResult(null)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to read file')
    } finally {
      // Allow re-selecting the same file later.
      e.target.value = ''
    }
  }

  async function handleImport() {
    if (!parsed?.ok) return
    try {
      const res = await importMutation.mutateAsync(parsed.payload)
      setResult(res)
      onImported?.(res)
      const summary = `Created ${res.created}, updated ${res.updated}, skipped ${res.skipped}`
      if (res.errors.length === 0) {
        toast.success(summary)
      } else {
        toast.warning(`${summary} — ${res.errors.length} error(s)`)
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Import failed')
    }
  }

  return (
    <>
      <DialogShell.Header
        icon={<Download className="size-5" />}
        title="Import MCP servers"
        description="Paste a Claude Desktop config or upload a JSON file. Secrets are never stored — wire credentials separately."
      />
      <DialogShell.Body>
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <label htmlFor="mcp-import-json">JSON payload</label>
            <Button size="sm" variant="outline" onClick={handlePickFile}>
              <FileJson className="size-3.5" /> Upload .json
            </Button>
            <input
              ref={fileInputRef}
              type="file"
              accept="application/json,.json"
              onChange={handleFile}
              className="hidden"
            />
          </div>
          <Textarea
            id="mcp-import-json"
            value={raw}
            rows={10}
            placeholder={`{\n  "mcpServers": {\n    "github": {\n      "command": "npx",\n      "args": ["-y", "@modelcontextprotocol/server-github"],\n      "env": { "GITHUB_TOKEN": "ghp_..." }\n    }\n  }\n}`}
            onChange={(e) => {
              setRaw(e.target.value)
              setResult(null)
            }}
            className="font-mono text-xs"
          />
          {parsed && !parsed.ok ? (
            <p className="text-xs text-destructive">{parsed.message}</p>
          ) : null}
          {parsed?.ok ? (
            <p className="text-xs text-muted-foreground">
              {parsed.names.length} server{parsed.names.length === 1 ? '' : 's'} ready:{' '}
              <span className="font-mono">{parsed.names.slice(0, 4).join(', ')}</span>
              {parsed.names.length > 4 ? ` +${parsed.names.length - 4} more` : ''}
            </p>
          ) : null}
        </div>

        <label className="flex cursor-pointer items-center gap-2 text-sm font-normal text-foreground">
          <Checkbox
            checked={overwrite}
            onCheckedChange={(checked) => setOverwrite(checked === true)}
          />
          Overwrite existing servers with the same name
        </label>

        {result ? <ImportResultSummary result={result} /> : null}
      </DialogShell.Body>
      <DialogShell.Footer>
        <FormFooter
          onCancel={onClose}
          onSubmit={handleImport}
          submitLabel={
            <>
              <Upload className="mr-1 size-4" /> Import
            </>
          }
          pending={importMutation.isPending}
          disabled={!canImport}
        />
      </DialogShell.Footer>
    </>
  )
}

function ImportResultSummary({ result }: { result: McpImportResult }) {
  return (
    <div className="space-y-2 rounded-lg border border-border/60 bg-muted/30 p-3">
      <div className="flex flex-wrap gap-2 text-xs">
        <span className="rounded-full bg-status-success/15 px-2 py-0.5 font-medium text-status-success">
          Created {result.created}
        </span>
        <span className="rounded-full bg-status-info/15 px-2 py-0.5 font-medium text-status-info">
          Updated {result.updated}
        </span>
        <span className="rounded-full bg-muted px-2 py-0.5 font-medium text-muted-foreground">
          Skipped {result.skipped}
        </span>
        {result.errors.length > 0 ? (
          <span className="rounded-full bg-status-danger/15 px-2 py-0.5 font-medium text-status-danger">
            {result.errors.length} error{result.errors.length === 1 ? '' : 's'}
          </span>
        ) : null}
      </div>
      {result.errors.length > 0 ? (
        <ul className="space-y-1 text-xs text-destructive">
          {result.errors.map((err) => (
            <li key={`${err.name}-${err.reason}`}>
              <span className="font-mono font-medium">{err.name}</span>: {err.reason}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
