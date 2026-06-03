'use client'

import type { CSSProperties, ReactNode } from 'react'

import { PageHeader } from '@/components/shared/page-header'
import { cn } from '@/lib/utils'

type ResourcePageProps = {
  title: string
  description?: string
  action?: ReactNode
  children: ReactNode
  className?: string
  contentClassName?: string
}

function ResourcePage({
  title,
  description,
  action,
  children,
  className,
  contentClassName,
}: ResourcePageProps) {
  return (
    <div
      className={cn(
        'flex min-h-0 flex-1 flex-col overflow-hidden bg-gradient-to-b from-emerald-50/35 via-background to-background dark:from-emerald-950/15 dark:via-background dark:to-background',
        'relative',
        className,
      )}
    >
      <div
        className={cn(
          'mx-auto flex min-h-0 w-full max-w-[1220px] flex-1 flex-col gap-5 px-5 py-6 md:px-8',
          contentClassName,
        )}
      >
        <PageHeader title={title} description={description} action={action} />
        {children}
      </div>
    </div>
  )
}

type ResourcePanelRootProps = {
  children: ReactNode
  className?: string
}

function ResourcePanelRoot({ children, className }: ResourcePanelRootProps) {
  return (
    <section
      className={cn(
        'flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-border/70 bg-[var(--moldy-surface-raised)] shadow-[var(--moldy-shadow-card)] backdrop-blur',
        className,
      )}
    >
      {children}
    </section>
  )
}

function ResourcePanelToolbar({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        'flex shrink-0 flex-col gap-3 border-b border-border/60 bg-card/55 p-4 md:p-5',
        className,
      )}
    >
      {children}
    </div>
  )
}

function ResourcePanelBody({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn('min-h-0 flex-1 overflow-y-auto bg-background/25 p-4 md:p-5', className)}>
      {children}
    </div>
  )
}

const ResourcePanel = Object.assign(ResourcePanelRoot, {
  Toolbar: ResourcePanelToolbar,
  Body: ResourcePanelBody,
})

type CountedLineTab = {
  value: string
  label: string
  countLabel?: string
  disabled?: boolean
}

type CountedLineTabsProps = {
  ariaLabel: string
  value: string
  tabs: CountedLineTab[]
  onValueChange: (value: string) => void
  className?: string
}

function CountedLineTabs({
  ariaLabel,
  value,
  tabs,
  onValueChange,
  className,
}: CountedLineTabsProps) {
  return (
    <div className={cn('flex items-end border-b border-border/70', className)}>
      <div
        role="tablist"
        aria-label={ariaLabel}
        className="flex min-w-0 flex-1 gap-5 overflow-x-auto"
      >
        {tabs.map((tab) => {
          const isActive = value === tab.value
          const activeLabel =
            isActive && tab.countLabel ? `${tab.label} ${tab.countLabel}` : tab.label
          return (
            <button
              key={tab.value || 'all'}
              type="button"
              role="tab"
              aria-selected={isActive}
              aria-label={activeLabel}
              disabled={tab.disabled}
              onClick={() => onValueChange(tab.value)}
              className={cn(
                'inline-flex h-9 shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 px-0.5 text-sm transition-colors',
                isActive
                  ? 'border-[var(--primary-strong)] font-semibold text-foreground'
                  : 'border-transparent font-medium text-muted-foreground hover:text-foreground',
                tab.disabled && 'cursor-not-allowed opacity-50 hover:text-muted-foreground',
              )}
            >
              <span>{tab.label}</span>
              {isActive && tab.countLabel ? (
                <span className="rounded-md bg-primary px-1.5 py-0.5 text-[11px] font-semibold leading-none text-primary-foreground ring-1 ring-primary-strong/15">
                  {tab.countLabel}
                </span>
              ) : null}
            </button>
          )
        })}
      </div>
    </div>
  )
}

function ResourceToolbar({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn('flex flex-col gap-2.5 sm:flex-row sm:items-center', className)}>
      {children}
    </div>
  )
}

type ResourceGridProps = {
  children: ReactNode
  minColumnWidth?: number
  className?: string
  style?: CSSProperties
}

function ResourceGrid({ children, minColumnWidth = 214, className, style }: ResourceGridProps) {
  return (
    <div
      className={cn('moldy-content-visibility grid gap-3', className)}
      style={{
        gridTemplateColumns: `repeat(auto-fill, minmax(${minColumnWidth}px, 1fr))`,
        ...style,
      }}
    >
      {children}
    </div>
  )
}

export { CountedLineTabs, ResourceGrid, ResourcePage, ResourcePanel, ResourceToolbar }
