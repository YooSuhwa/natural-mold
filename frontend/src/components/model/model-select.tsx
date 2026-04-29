'use client'

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useModels } from '@/lib/hooks/use-models'

interface ModelSelectProps {
  value?: string
  onValueChange: (id: string) => void
  className?: string
  placeholder?: string
}

/**
 * Minimal model picker against the read-only `/api/models` catalog.
 */
export function ModelSelect({
  value,
  onValueChange,
  className,
  placeholder = 'Select a model',
}: ModelSelectProps) {
  const { data: models, isLoading } = useModels()

  return (
    <Select
      value={value}
      onValueChange={(v) => v && onValueChange(v)}
      disabled={isLoading}
    >
      <SelectTrigger className={className}>
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {(models ?? []).map((m) => (
          <SelectItem key={m.id} value={m.id}>
            <span className="flex items-center gap-2">
              <span>{m.display_name}</span>
              <span className="text-[10px] text-muted-foreground">{m.provider}</span>
            </span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
