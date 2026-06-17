'use client'

import { Button } from '@/components/ui/button'
import type { SkillStateFilter } from '@/lib/skill-state-filters'
import { cn } from '@/lib/utils'

type SkillStateFilterChip = {
  readonly value: SkillStateFilter
  readonly label: string
  readonly countLabel: string
}

type SkillStateFilterChipsProps = {
  readonly ariaLabel: string
  readonly value: SkillStateFilter
  readonly filters: readonly SkillStateFilterChip[]
  readonly onValueChange: (value: SkillStateFilter) => void
  readonly className?: string
}

export function SkillStateFilterChips({
  ariaLabel,
  value,
  filters,
  onValueChange,
  className,
}: SkillStateFilterChipsProps) {
  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className={cn('flex min-w-0 flex-wrap items-center gap-1.5', className)}
    >
      {filters.map((filter) => {
        const active = value === filter.value
        return (
          <Button
            key={filter.value}
            type="button"
            variant={active ? 'default' : 'outline'}
            size="sm"
            aria-pressed={active}
            aria-label={`${filter.label} ${filter.countLabel}`}
            onClick={() => onValueChange(filter.value)}
            className="h-7 gap-1.5 px-2"
          >
            <span>{filter.label}</span>
            <span
              className={cn(
                'rounded-md px-1.5 py-0.5 moldy-ui-caption font-semibold leading-none',
                active
                  ? 'bg-primary-foreground/20 text-primary-foreground'
                  : 'bg-muted text-foreground',
              )}
            >
              {filter.countLabel}
            </span>
          </Button>
        )
      })}
    </div>
  )
}
