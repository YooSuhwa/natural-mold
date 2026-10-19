'use client'

import { FormFieldShell } from '@/components/shared/form-field-shell'
import { Select, SelectContent, SelectItem, SelectTrigger } from '@/components/ui/select'

interface OrgSelectFieldProps<Value extends string> {
  id: string
  label: string
  description?: string
  value: Value
  options: readonly Value[]
  optionLabel: (value: Value) => string
  disabled?: boolean
  onChange: (value: Value) => void
}

/** A labelled single-choice field over a fixed option list (org settings forms). */
export function OrgSelectField<Value extends string>({
  id,
  label,
  description,
  value,
  options,
  optionLabel,
  disabled,
  onChange,
}: OrgSelectFieldProps<Value>) {
  return (
    <FormFieldShell id={id} label={label} description={description}>
      <Select
        value={value}
        disabled={disabled}
        onValueChange={(next) => {
          const match = options.find((option) => option === next)
          if (match !== undefined) onChange(match)
        }}
      >
        <SelectTrigger id={id} className="w-full sm:w-64">
          <span>{optionLabel(value)}</span>
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => (
            <SelectItem key={option} value={option}>
              {optionLabel(option)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </FormFieldShell>
  )
}
