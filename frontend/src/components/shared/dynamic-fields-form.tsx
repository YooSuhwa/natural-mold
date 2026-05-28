'use client'

import { useState, type ReactNode } from 'react'
import { Eye, EyeOff, AlertCircle, Clock } from 'lucide-react'

import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Checkbox } from '@/components/ui/checkbox'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'
import type { FieldDef, FieldOption } from '@/lib/types/credential'

export interface DynamicFieldsFormProps {
  fields: FieldDef[]
  value: Record<string, unknown>
  onChange: (next: Record<string, unknown>) => void
  errors?: Record<string, string>
  disabled?: boolean
  /** Optional click handler for `oauth_button` fields. Receives the field. */
  onOAuthClick?: (field: FieldDef) => void
}

/**
 * Validate a single value against the FieldDef's typeOptions/required rules.
 * Returns an error message string, or null if valid.
 */
export function validateField(field: FieldDef, raw: unknown): string | null {
  const isEmpty = raw === undefined || raw === null || raw === ''
  if (field.required && isEmpty) return `${field.display_name} is required`
  if (isEmpty) return null

  const opts = field.type_options ?? {}

  if (field.kind === 'string' || field.kind === 'password' || field.kind === 'multiline') {
    const s = String(raw)
    if (opts.min_length !== undefined && s.length < opts.min_length) {
      return `Minimum length is ${opts.min_length}`
    }
    if (opts.max_length !== undefined && s.length > opts.max_length) {
      return `Maximum length is ${opts.max_length}`
    }
    if (opts.regex) {
      try {
        if (!new RegExp(opts.regex).test(s)) return 'Format is invalid'
      } catch {
        // ignore bad regex from server
      }
    }
  }

  if (field.kind === 'number') {
    const n = Number(raw)
    if (Number.isNaN(n)) return 'Must be a number'
    if (opts.min !== undefined && n < opts.min) return `Minimum is ${opts.min}`
    if (opts.max !== undefined && n > opts.max) return `Maximum is ${opts.max}`
  }

  if (field.kind === 'json') {
    try {
      JSON.parse(typeof raw === 'string' ? raw : JSON.stringify(raw))
    } catch {
      return 'Invalid JSON'
    }
  }

  return null
}

/** Validate all fields and return a `field.name -> message` map.
 *
 * @param skipRequired - when true, skips `required` checks (e.g. tool creation
 *   where required params are filled by the agent at runtime, not the user). */
export function validateFields(
  fields: FieldDef[],
  values: Record<string, unknown>,
  { skipRequired = false }: { skipRequired?: boolean } = {},
): Record<string, string> {
  const errors: Record<string, string> = {}
  for (const f of fields) {
    if (!shouldShow(f, values)) continue
    const fieldToValidate = skipRequired ? { ...f, required: false } : f
    const message = validateField(fieldToValidate, values[f.name])
    if (message) errors[f.name] = message
  }
  return errors
}

function shouldShow(field: FieldDef, values: Record<string, unknown>): boolean {
  const show = field.display_options?.show
  const hide = field.display_options?.hide
  if (show) {
    for (const [parent, allowed] of Object.entries(show)) {
      const v = values[parent]
      if (!allowed.includes(v as string | number | boolean)) return false
    }
  }
  if (hide) {
    for (const [parent, blocked] of Object.entries(hide)) {
      const v = values[parent]
      if (blocked.includes(v as string | number | boolean)) return false
    }
  }
  return true
}

export function DynamicFieldsForm({
  fields,
  value,
  onChange,
  errors,
  disabled,
  onOAuthClick,
}: DynamicFieldsFormProps) {
  const setField = (name: string, next: unknown) => {
    onChange({ ...value, [name]: next })
  }

  return (
    <div className="space-y-4">
      {fields.map((field) => {
        if (!shouldShow(field, value)) return null
        return (
          <FieldRow
            key={field.name}
            field={field}
            value={value[field.name]}
            error={errors?.[field.name]}
            disabled={disabled}
            onChange={(next) => setField(field.name, next)}
            onOAuthClick={onOAuthClick}
          />
        )
      })}
    </div>
  )
}

function FieldRow({
  field,
  value,
  error,
  disabled,
  onChange,
  onOAuthClick,
}: {
  field: FieldDef
  value: unknown
  error?: string
  disabled?: boolean
  onChange: (next: unknown) => void
  onOAuthClick?: (field: FieldDef) => void
}) {
  return (
    <div className="space-y-1.5">
      <FieldLabel field={field} />
      <FieldControl
        field={field}
        value={value}
        disabled={disabled}
        onChange={onChange}
        onOAuthClick={onOAuthClick}
      />
      {field.description && !error && (
        <p className="text-xs text-muted-foreground">{field.description}</p>
      )}
      {error && (
        <p className="flex items-center gap-1 text-xs text-destructive">
          <AlertCircle className="size-3" aria-hidden />
          {error}
        </p>
      )}
    </div>
  )
}

function FieldLabel({ field }: { field: FieldDef }) {
  return (
    <label
      htmlFor={`field-${field.name}`}
      className="flex items-center gap-1 text-xs font-medium text-foreground"
    >
      {field.display_name}
      {field.required && <span className="text-destructive">*</span>}
      {field.type_options?.expirable && (
        <span className="ml-1 inline-flex items-center gap-1 text-muted-foreground">
          <Clock className="size-3" aria-hidden />
        </span>
      )}
    </label>
  )
}

function FieldControl({
  field,
  value,
  disabled,
  onChange,
  onOAuthClick,
}: {
  field: FieldDef
  value: unknown
  disabled?: boolean
  onChange: (next: unknown) => void
  onOAuthClick?: (field: FieldDef) => void
}): ReactNode {
  const id = `field-${field.name}`
  const opts = field.type_options ?? {}
  const isPasswordField = field.kind === 'password' || opts.password === true

  switch (field.kind) {
    case 'string':
    case 'password':
      return (
        <PasswordOrText
          id={id}
          value={value as string | undefined}
          placeholder={field.placeholder ?? ''}
          disabled={disabled}
          masked={isPasswordField}
          onChange={onChange}
        />
      )
    case 'number':
      return (
        <Input
          id={id}
          type="number"
          value={(value as number | string | undefined) ?? ''}
          placeholder={field.placeholder ?? ''}
          disabled={disabled}
          min={opts.min}
          max={opts.max}
          step={opts.step}
          onChange={(e) => onChange(e.target.value === '' ? undefined : Number(e.target.value))}
        />
      )
    case 'select':
      return (
        <Select
          value={(value as string | undefined) ?? ''}
          onValueChange={(v) => onChange(v)}
          disabled={disabled}
        >
          <SelectTrigger id={id} className="w-full">
            <SelectValue placeholder={field.placeholder ?? 'Select...'} />
          </SelectTrigger>
          <SelectContent>
            {(field.options ?? []).map((opt: FieldOption) => (
              <SelectItem key={String(opt.value)} value={String(opt.value)}>
                {opt.name ?? String(opt.value)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )
    case 'multiline':
      return (
        <Textarea
          id={id}
          value={(value as string | undefined) ?? ''}
          placeholder={field.placeholder ?? ''}
          disabled={disabled}
          rows={opts.rows ?? 4}
          onChange={(e) => onChange(e.target.value)}
        />
      )
    case 'json':
      return (
        <Textarea
          id={id}
          value={
            typeof value === 'string'
              ? value
              : value === undefined
                ? ''
                : JSON.stringify(value, null, 2)
          }
          placeholder={field.placeholder ?? '{ }'}
          disabled={disabled}
          rows={opts.rows ?? 6}
          className="font-mono text-xs"
          onChange={(e) => onChange(e.target.value)}
        />
      )
    case 'toggle':
      return (
        <label className="inline-flex items-center gap-2 text-sm">
          <Checkbox
            id={id}
            checked={Boolean(value)}
            disabled={disabled}
            onCheckedChange={(checked) => onChange(Boolean(checked))}
          />
          <span className="text-foreground/80">{field.placeholder ?? 'Enabled'}</span>
        </label>
      )
    case 'oauth_button':
      return (
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled}
          onClick={() => onOAuthClick?.(field)}
        >
          {field.display_name}
        </Button>
      )
    case 'collection':
      return (
        <Card className={cn('p-3 space-y-3 bg-muted/30')}>
          <DynamicFieldsForm
            fields={(field.options as unknown as FieldDef[]) ?? []}
            value={(value as Record<string, unknown>) ?? {}}
            onChange={(next) => onChange(next)}
            disabled={disabled}
          />
        </Card>
      )
    default:
      return (
        <Input
          id={id}
          value={(value as string | undefined) ?? ''}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
        />
      )
  }
}

function PasswordOrText({
  id,
  value,
  placeholder,
  disabled,
  masked,
  onChange,
}: {
  id: string
  value?: string
  placeholder?: string
  disabled?: boolean
  masked: boolean
  onChange: (next: string) => void
}) {
  const [reveal, setReveal] = useState(false)
  if (!masked) {
    return (
      <Input
        id={id}
        value={value ?? ''}
        placeholder={placeholder}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      />
    )
  }
  return (
    <div className="relative">
      <Input
        id={id}
        type={reveal ? 'text' : 'password'}
        value={value ?? ''}
        placeholder={placeholder ?? '••••••••'}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className="pr-10"
      />
      <button
        type="button"
        onClick={() => setReveal((v) => !v)}
        className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-muted-foreground hover:text-foreground"
        aria-label={reveal ? 'Hide value' : 'Reveal value'}
      >
        {reveal ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
      </button>
    </div>
  )
}
