'use client'

import type { ReactNode } from 'react'

import { Dialog, DialogContent, DialogTitle, DialogDescription } from '@/components/ui/dialog'
import {
  DIALOG_SIZE,
  DIALOG_HEIGHT,
  type DialogSize,
  type DialogHeight,
} from '@/lib/design-tokens'
import { cn } from '@/lib/utils'

interface DialogShellProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  size?: DialogSize
  height?: DialogHeight
  className?: string
  children: ReactNode
}

function Root({
  open,
  onOpenChange,
  size = 'md',
  height = 'fixed',
  className,
  children,
}: DialogShellProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={cn(
          'flex max-w-[calc(100%-2rem)] flex-col gap-0 overflow-hidden p-0 sm:max-w-none',
          DIALOG_SIZE[size],
          DIALOG_HEIGHT[height],
          className,
        )}
      >
        {children}
      </DialogContent>
    </Dialog>
  )
}

interface HeaderProps {
  icon?: ReactNode
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
  className?: string
}

function Header({ icon, title, description, actions, className }: HeaderProps) {
  return (
    <div
      className={cn(
        'flex items-start gap-4 border-b border-border/60 px-6 py-5',
        className,
      )}
    >
      {icon ? (
        <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary-strong/15 text-primary-strong">
          {icon}
        </div>
      ) : null}
      <div className="min-w-0 flex-1">
        <DialogTitle className="text-base font-semibold tracking-tight text-foreground">
          {title}
        </DialogTitle>
        {description ? (
          <DialogDescription className="mt-1 text-sm text-muted-foreground">
            {description}
          </DialogDescription>
        ) : null}
      </div>
      {actions ? (
        <div className="ml-auto flex shrink-0 items-center gap-2">{actions}</div>
      ) : null}
    </div>
  )
}

interface BodyProps {
  className?: string
  children: ReactNode
}

function Body({ className, children }: BodyProps) {
  return (
    <div
      className={cn(
        'flex-1 space-y-6 overflow-y-auto px-6 py-5 [&_label]:text-xs [&_label]:font-medium [&_label]:text-muted-foreground',
        className,
      )}
    >
      {children}
    </div>
  )
}

interface FooterProps {
  className?: string
  children: ReactNode
}

function Footer({ className, children }: FooterProps) {
  return (
    <div
      className={cn(
        'flex shrink-0 items-center justify-end gap-2 border-t border-border/60 bg-muted/30 px-6 py-4',
        className,
      )}
    >
      {children}
    </div>
  )
}

interface SidebarProps {
  className?: string
  children: ReactNode
}

function Sidebar({ className, children }: SidebarProps) {
  return (
    <aside
      className={cn(
        'w-[260px] shrink-0 overflow-y-auto border-r border-border/60 bg-muted/30 px-4 py-5',
        className,
      )}
    >
      {children}
    </aside>
  )
}

interface SplitProps {
  className?: string
  children: ReactNode
}

function Split({ className, children }: SplitProps) {
  return <div className={cn('flex flex-1 overflow-hidden', className)}>{children}</div>
}

export const DialogShell = Object.assign(Root, {
  Header,
  Body,
  Footer,
  Sidebar,
  Split,
})
