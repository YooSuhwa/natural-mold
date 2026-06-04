'use client'

import type { ComponentPropsWithoutRef, CSSProperties, ReactNode } from 'react'
import { ChevronRightIcon } from 'lucide-react'

import { PageHeader } from '@/components/shared/page-header'
import {
  resourceCardClassName,
  resourceMetaClassName,
  type ResourceCardDensity,
  type ResourceTone,
} from '@/lib/resource-tones'
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
        'moldy-app-surface flex min-h-0 flex-1 flex-col overflow-hidden',
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
  return <section className={cn('moldy-resource-panel', className)}>{children}</section>
}

function ResourcePanelToolbar({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div className={cn('moldy-resource-panel-toolbar md:p-5', className)}>{children}</div>
  )
}

function ResourcePanelBody({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn('moldy-resource-panel-body md:p-5', className)}>{children}</div>
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
                <span className="rounded-md bg-primary px-1.5 py-0.5 moldy-ui-caption font-semibold leading-none text-primary-foreground ring-1 ring-primary-strong/15">
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

function ResourceSummaryStrip({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('moldy-resource-summary-strip sm:grid-cols-3', className)}>{children}</div>
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

function ResourceBadge({
  tone,
  children,
  className,
}: {
  tone: ResourceTone
  children: ReactNode
  className?: string
}) {
  return (
    <span className={cn('moldy-resource-badge', tone.badge, className)}>
      <span className={cn('moldy-resource-badge-dot', tone.dot)} />
      <span className="truncate">{children}</span>
    </span>
  )
}

type ResourceListCardRootProps = {
  as?: 'article' | 'button'
  tone: ResourceTone
  density?: ResourceCardDensity
  interactive?: boolean
  children: ReactNode
  className?: string
  type?: 'button' | 'submit' | 'reset'
  disabled?: boolean
} & Omit<ComponentPropsWithoutRef<'article'>, 'className'> &
  Omit<ComponentPropsWithoutRef<'button'>, 'className' | 'type' | 'disabled'>

function ResourceListCardRoot({
  as = 'article',
  tone,
  density = 'standard',
  interactive,
  children,
  className,
  type = 'button',
  disabled,
  ...props
}: ResourceListCardRootProps) {
  const classes = resourceCardClassName(tone, className, {
    density,
    interactive: interactive ?? as === 'button',
  })

  if (as === 'button') {
    return (
      <button
        type={type}
        disabled={disabled}
        className={classes}
        {...(props as ComponentPropsWithoutRef<'button'>)}
      >
        {children}
      </button>
    )
  }

  return (
    <article className={classes} {...(props as ComponentPropsWithoutRef<'article'>)}>
      {children}
    </article>
  )
}

function ResourceListCardHeader({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return <div className={cn('moldy-resource-card-header', className)}>{children}</div>
}

function ResourceListCardStatusRow({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return <div className={cn('moldy-resource-card-status-row', className)}>{children}</div>
}

function ResourceListCardMetaRow({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return <div className={cn('moldy-resource-card-meta-row', className)}>{children}</div>
}

function ResourceListCardFooter({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return <div className={cn('moldy-resource-card-footer', className)}>{children}</div>
}

const ResourceListCard = Object.assign(ResourceListCardRoot, {
  Header: ResourceListCardHeader,
  Title: ResourceCardTitle,
  Subhead: ResourceCardSubtext,
  Description: ResourceCardDescription,
  StatusRow: ResourceListCardStatusRow,
  MetaRow: ResourceListCardMetaRow,
  Footer: ResourceListCardFooter,
})

function ResourceCardTitle({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cn('moldy-resource-title', className)}>{children}</span>
}

function ResourceCardDescription({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return <p className={cn('moldy-resource-description', className)}>{children}</p>
}

function ResourceCardSubtext({
  children,
  tone = 'default',
  className,
}: {
  children: ReactNode
  tone?: 'default' | 'mono'
  className?: string
}) {
  return (
    <p
      className={cn('moldy-resource-subtext', className)}
      data-mono={tone === 'mono' ? 'true' : undefined}
    >
      {children}
    </p>
  )
}

function ResourceCardMeta({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cn(resourceMetaClassName, className)}>{children}</span>
}

function ResourceMetaStack({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('moldy-resource-meta-stack space-y-1', className)}>{children}</div>
}

function ResourceCardAction({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span className={cn('moldy-resource-action', className)}>
      {children}
      <ChevronRightIcon aria-hidden className="size-3" />
    </span>
  )
}

export {
  CountedLineTabs,
  ResourceBadge,
  ResourceCardAction,
  ResourceCardDescription,
  ResourceCardMeta,
  ResourceCardSubtext,
  ResourceCardTitle,
  ResourceGrid,
  ResourceListCard,
  ResourceMetaStack,
  ResourcePage,
  ResourcePanel,
  ResourceSummaryStrip,
  ResourceToolbar,
}
