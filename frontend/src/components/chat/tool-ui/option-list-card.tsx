'use client'

import { useCallback, useMemo, useState } from 'react'
import { CheckIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import {
  serializeOptionListResponse,
  type SerializedUserInputResponse,
} from '@/lib/chat/decision-mappers'
import type { UserInputOption } from '@/lib/types'

interface OptionListCardProps {
  id: string
  title?: string
  options: UserInputOption[]
  minSelections?: number
  maxSelections?: number
  submitting: boolean
  onInteract: () => void
  onSubmit: (response: SerializedUserInputResponse) => void | Promise<void>
}

function optionId(option: UserInputOption): string {
  return option.id ?? option.label
}

export function OptionListCard({
  id,
  title,
  options,
  minSelections = 1,
  maxSelections,
  submitting,
  onInteract,
  onSubmit,
}: OptionListCardProps) {
  const t = useTranslations('chat.userInput')
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const selected = useMemo(() => new Set(selectedIds), [selectedIds])
  const selectionMode = maxSelections === 1 ? 'single' : 'multi'
  const canConfirm = selectedIds.length >= minSelections

  const toggleOption = useCallback(
    (option: UserInputOption) => {
      if (option.disabled || submitting) return
      const oid = optionId(option)
      setSelectedIds((prev) => {
        if (selectionMode === 'single') return prev.includes(oid) ? [] : [oid]
        if (prev.includes(oid)) return prev.filter((id) => id !== oid)
        if (maxSelections !== undefined && prev.length >= maxSelections) return prev
        return [...prev, oid]
      })
      onInteract()
    },
    [maxSelections, onInteract, selectionMode, submitting],
  )

  const clear = useCallback(() => {
    setSelectedIds([])
    onInteract()
  }, [onInteract])

  const submit = useCallback(() => {
    if (!canConfirm || submitting) return
    void onSubmit(serializeOptionListResponse(options, selectedIds))
  }, [canConfirm, onSubmit, options, selectedIds, submitting])

  return (
    <div className="space-y-4" data-tool-ui-id={id}>
      <div className="space-y-1">
        {title && <p className="text-sm font-semibold">{title}</p>}
        {maxSelections !== undefined && selectionMode === 'multi' && (
          <p className="text-xs text-muted-foreground">
            {t('selectionRange', { min: minSelections, max: maxSelections })}
          </p>
        )}
      </div>

      <div className="space-y-1.5" role="listbox" aria-multiselectable={selectionMode === 'multi'}>
        {options.map((option) => {
          const oid = optionId(option)
          const checked = selected.has(oid)
          const locked =
            selectionMode === 'multi' &&
            maxSelections !== undefined &&
            selectedIds.length >= maxSelections &&
            !checked
          const disabled = option.disabled || locked
          return (
            <button
              key={oid}
              type="button"
              disabled={disabled || submitting}
              onClick={() => toggleOption(option)}
              className={cn(
                'flex min-h-11 w-full items-start gap-3 rounded-lg border px-3 py-2 text-left text-sm transition-colors',
                checked
                  ? 'border-primary/60 bg-primary/10 text-primary-strong'
                  : 'border-border hover:border-primary/50 hover:bg-accent',
                (disabled || submitting) && 'cursor-not-allowed opacity-50',
              )}
              role="option"
              aria-selected={checked}
            >
              <span
                className={cn(
                  'mt-0.5 flex size-4 shrink-0 items-center justify-center border-2',
                  selectionMode === 'single' ? 'rounded-full' : 'rounded',
                  checked
                    ? 'border-primary-strong bg-primary-strong text-white'
                    : 'border-muted-foreground/50',
                )}
              >
                {selectionMode === 'single'
                  ? checked && <span className="size-1.5 rounded-full bg-current" />
                  : checked && <CheckIcon className="size-3" />}
              </span>
              <span className="min-w-0">
                <span className="block font-medium leading-5">{option.label}</span>
                {option.description && (
                  <span className="block text-xs leading-5 text-muted-foreground">
                    {option.description}
                  </span>
                )}
              </span>
            </button>
          )
        })}
      </div>

      <div className="flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={clear}
          disabled={selectedIds.length === 0 || submitting}
          className={cn(
            'rounded-full px-3 py-2 text-xs font-medium transition-colors',
            selectedIds.length === 0 || submitting
              ? 'cursor-not-allowed text-muted-foreground/50'
              : 'text-muted-foreground hover:bg-muted hover:text-foreground',
          )}
        >
          {t('clear')}
        </button>
        <button
          type="button"
          onClick={submit}
          disabled={!canConfirm || submitting}
          className={cn(
            'rounded-full px-4 py-2 text-xs font-medium transition-colors',
            canConfirm && !submitting
              ? 'bg-primary text-primary-foreground hover:bg-primary/90'
              : 'cursor-not-allowed bg-muted text-muted-foreground',
          )}
        >
          {submitting ? t('sending') : t('confirmSelection', { count: selectedIds.length })}
        </button>
      </div>
    </div>
  )
}
