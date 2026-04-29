'use client'

import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Loader2, Trash2 } from 'lucide-react'

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
import { Checkbox } from '@/components/ui/checkbox'
import { Separator } from '@/components/ui/separator'
import { ModelSourceBadge } from './model-source-badge'
import {
  perMillionToTokenPrice,
  tokenPriceToPerMillion,
} from './model-format'
import { useDeleteModel, useUpdateModel } from '@/lib/hooks/use-models'
import type { Model } from '@/lib/types/model'

interface ModelEditDialogProps {
  model: Model | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function ModelEditDialog({
  model,
  open,
  onOpenChange,
}: ModelEditDialogProps) {
  const update = useUpdateModel()
  const remove = useDeleteModel()

  const [displayName, setDisplayName] = useState('')
  const [inputPriceM, setInputPriceM] = useState<string>('')
  const [outputPriceM, setOutputPriceM] = useState<string>('')
  const [contextWindow, setContextWindow] = useState<string>('')
  const [maxOutputTokens, setMaxOutputTokens] = useState<string>('')
  const [supportsVision, setSupportsVision] = useState(false)
  const [supportsTools, setSupportsTools] = useState(false)
  const [supportsReasoning, setSupportsReasoning] = useState(false)
  const [isDefault, setIsDefault] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)

  // Re-seed the form whenever a different model is opened. Sync from props
  // (parent owns the canonical Model row) is the textbook valid use of an
  // effect; the pragma silences the React Compiler heuristic.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (!model) return
    setDisplayName(model.display_name)
    const ip = tokenPriceToPerMillion(model.cost_per_input_token)
    const op = tokenPriceToPerMillion(model.cost_per_output_token)
    setInputPriceM(ip === '' ? '' : String(ip))
    setOutputPriceM(op === '' ? '' : String(op))
    setContextWindow(
      model.context_window === null ? '' : String(model.context_window),
    )
    setMaxOutputTokens(
      model.max_output_tokens === null ? '' : String(model.max_output_tokens),
    )
    setSupportsVision(Boolean(model.supports_vision))
    setSupportsTools(Boolean(model.supports_function_calling))
    setSupportsReasoning(Boolean(model.supports_reasoning))
    setIsDefault(model.is_default)
    setConfirmDelete(false)
  }, [model])
  /* eslint-enable react-hooks/set-state-in-effect */

  if (!model) return null

  async function handleSave() {
    if (!model) return
    try {
      await update.mutateAsync({
        id: model.id,
        data: {
          display_name: displayName.trim() || model.display_name,
          cost_per_input_token: perMillionToTokenPrice(inputPriceM),
          cost_per_output_token: perMillionToTokenPrice(outputPriceM),
          context_window: contextWindow ? Number(contextWindow) : null,
          max_output_tokens: maxOutputTokens ? Number(maxOutputTokens) : null,
          supports_vision: supportsVision,
          supports_function_calling: supportsTools,
          supports_reasoning: supportsReasoning,
          is_default: isDefault,
        },
      })
      toast.success('Model updated')
      onOpenChange(false)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Save failed')
    }
  }

  async function handleDelete() {
    if (!model) return
    try {
      await remove.mutateAsync(model.id)
      toast.success('Model deleted')
      onOpenChange(false)
    } catch (e) {
      const err = e as { status?: number; message?: string }
      if (err.status === 409) {
        toast.error(
          'Cannot delete: this model is in use. Update the affected agents to a different model first.',
        )
      } else {
        toast.error(err.message ?? 'Delete failed')
      }
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {model.display_name}
            <ModelSourceBadge source={model.source} />
          </DialogTitle>
          <DialogDescription className="font-mono text-xs">
            {model.provider} · {model.model_name}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <label htmlFor="e-display" className="text-xs font-medium">
              Display name
            </label>
            <Input
              id="e-display"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label htmlFor="e-input-price" className="text-xs font-medium">
                Input $/1M tokens
              </label>
              <Input
                id="e-input-price"
                type="number"
                step="0.01"
                min="0"
                value={inputPriceM}
                onChange={(e) => setInputPriceM(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="e-output-price" className="text-xs font-medium">
                Output $/1M tokens
              </label>
              <Input
                id="e-output-price"
                type="number"
                step="0.01"
                min="0"
                value={outputPriceM}
                onChange={(e) => setOutputPriceM(e.target.value)}
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label htmlFor="e-context" className="text-xs font-medium">
                Context window
              </label>
              <Input
                id="e-context"
                type="number"
                step="1024"
                min="0"
                value={contextWindow}
                onChange={(e) => setContextWindow(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="e-max-out" className="text-xs font-medium">
                Max output tokens
              </label>
              <Input
                id="e-max-out"
                type="number"
                step="256"
                min="0"
                value={maxOutputTokens}
                onChange={(e) => setMaxOutputTokens(e.target.value)}
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <Toggle
              id="e-vision"
              label="Vision input"
              checked={supportsVision}
              onChange={setSupportsVision}
            />
            <Toggle
              id="e-tools"
              label="Function calling"
              checked={supportsTools}
              onChange={setSupportsTools}
            />
            <Toggle
              id="e-reasoning"
              label="Reasoning"
              checked={supportsReasoning}
              onChange={setSupportsReasoning}
            />
            <Toggle
              id="e-default"
              label="Set as default"
              checked={isDefault}
              onChange={setIsDefault}
            />
          </div>

          <Separator />

          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>
              In use by {model.agent_count} agent
              {model.agent_count === 1 ? '' : 's'}
            </span>
            {!confirmDelete ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setConfirmDelete(true)}
                className="text-destructive hover:text-destructive"
              >
                <Trash2 className="size-3.5" />
                Delete
              </Button>
            ) : (
              <div className="flex items-center gap-2">
                <span className="text-destructive">Delete this model?</span>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setConfirmDelete(false)}
                >
                  Cancel
                </Button>
                <Button
                  size="sm"
                  variant="destructive"
                  onClick={handleDelete}
                  disabled={remove.isPending}
                >
                  Confirm
                </Button>
              </div>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={update.isPending}>
            {update.isPending && <Loader2 className="size-4 animate-spin" />}
            Save changes
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function Toggle({
  id,
  label,
  checked,
  onChange,
}: {
  id: string
  label: string
  checked: boolean
  onChange: (next: boolean) => void
}) {
  return (
    <label
      htmlFor={id}
      className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm"
    >
      <Checkbox
        id={id}
        checked={checked}
        onCheckedChange={(next) => onChange(Boolean(next))}
      />
      {label}
    </label>
  )
}
