'use client'

import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import type { WorkflowInput } from '@/lib/types/workflow'

export type InputValues = Record<string, string | boolean>

function typed(input: WorkflowInput, raw: string | boolean | undefined): unknown {
  if (input.type === 'boolean') return raw === true
  if (raw === undefined || raw === '') return undefined
  if (input.type === 'number' || input.type === 'integer') return Number(raw)
  return raw
}

/** The form values as run inputs (empty fields left out). */
export function typedInputs(inputs: WorkflowInput[], values: InputValues): Record<string, unknown> {
  const payload: Record<string, unknown> = {}
  for (const input of inputs) {
    const value = typed(input, values[input.name])
    if (value !== undefined) payload[input.name] = value
  }
  return payload
}

/** A workflow's inputs as form fields (a run, a schedule). ``onPickFile``: file inputs. */
export function RunInputFields({
  inputs,
  values,
  onChange,
  onPickFile,
}: {
  inputs: WorkflowInput[]
  values: InputValues
  onChange: (name: string, value: string | boolean) => void
  onPickFile?: (name: string, file: File | undefined) => void
}) {
  return (
    <>
      {inputs.map((input) => (
        <label key={input.name} className="flex flex-col gap-1.5 text-sm">
          <span>
            {input.label || input.name}
            {input.required ? <span className="text-status-danger"> *</span> : null}
          </span>
          {input.type === 'boolean' ? (
            <Checkbox
              checked={values[input.name] === true}
              onCheckedChange={(checked) => onChange(input.name, checked === true)}
            />
          ) : input.type === 'file' ? (
            <Input
              type="file"
              onChange={(event) => onPickFile?.(input.name, event.target.files?.[0])}
            />
          ) : input.type === 'string' ? (
            <Textarea
              rows={3}
              maxLength={input.max_length ?? undefined}
              value={String(values[input.name] ?? '')}
              onChange={(event) => onChange(input.name, event.target.value)}
            />
          ) : (
            <Input
              type="number"
              step={input.type === 'integer' ? 1 : 'any'}
              value={String(values[input.name] ?? '')}
              onChange={(event) => onChange(input.name, event.target.value)}
            />
          )}
        </label>
      ))}
    </>
  )
}
