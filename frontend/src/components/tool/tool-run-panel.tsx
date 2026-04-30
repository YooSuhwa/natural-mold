'use client'

import { useState } from 'react'
import { toast } from 'sonner'
import { Play, Loader2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { useRunTool } from '@/lib/hooks/use-tools'

interface ToolRunPanelProps {
  toolId: string
}

export function ToolRunPanel({ toolId }: ToolRunPanelProps) {
  const [argsText, setArgsText] = useState('{}')
  const [result, setResult] = useState<string | null>(null)
  const run = useRunTool()

  async function handleRun() {
    let parsed: Record<string, unknown> = {}
    try {
      const raw = argsText.trim()
      parsed = raw ? JSON.parse(raw) : {}
    } catch {
      toast.error('Runtime args must be valid JSON')
      return
    }
    try {
      const out = await run.mutateAsync({ id: toolId, runtime_args: parsed })
      setResult(JSON.stringify(out, null, 2))
      if (out.success) {
        toast.success(`Ran in ${out.duration_ms}ms`)
      } else {
        toast.error(out.error ?? 'Run failed')
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Run failed')
    }
  }

  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <label htmlFor="tool-args" className="text-xs font-medium">
          Runtime args (JSON)
        </label>
        <Textarea
          id="tool-args"
          value={argsText}
          rows={5}
          className="font-mono text-xs"
          onChange={(e) => setArgsText(e.target.value)}
        />
      </div>
      <Button size="sm" onClick={handleRun} disabled={run.isPending}>
        {run.isPending ? (
          <Loader2 className="size-4 animate-spin" />
        ) : (
          <Play className="size-4" />
        )}
        Run
      </Button>
      {result && (
        <pre className="max-h-64 overflow-auto rounded border bg-muted/40 p-2 font-mono text-[11px]">
          {result}
        </pre>
      )}
    </div>
  )
}
