'use client'

import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'

export interface CountedTabItem {
  value: string
  label: string
  count?: number
  countLabel?: string
  disabled?: boolean
}

interface CountedTabsProps {
  value: string
  onValueChange: (value: string) => void
  tabs: CountedTabItem[]
  ariaLabel: string
}

export function CountedTabs({ value, onValueChange, tabs, ariaLabel }: CountedTabsProps) {
  return (
    <Tabs value={value} onValueChange={(nextValue) => onValueChange(String(nextValue))}>
      <TabsList variant="line" aria-label={ariaLabel} className="h-auto justify-start">
        {tabs.map((tab) => {
          const countLabel =
            tab.countLabel ?? (typeof tab.count === 'number' ? String(tab.count) : undefined)

          return (
            <TabsTrigger
              key={tab.value}
              value={tab.value}
              disabled={tab.disabled}
              aria-label={countLabel ? `${tab.label} ${countLabel}` : tab.label}
              className="px-4 py-2.5 after:bg-primary-strong data-active:text-primary-strong"
            >
              <span>{tab.label}</span>
              {countLabel ? (
                <span className="moldy-ui-caption tabular-nums text-muted-foreground">
                  {countLabel}
                </span>
              ) : null}
            </TabsTrigger>
          )
        })}
      </TabsList>
    </Tabs>
  )
}
