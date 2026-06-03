'use client'

import type {
  ButtonHTMLAttributes,
  HTMLAttributes,
  ReactNode,
  TextareaHTMLAttributes,
} from 'react'

import { cn } from '@/lib/utils'

type BuilderButtonTone = 'primary' | 'secondary' | 'ghost'

function BuilderHeaderIcon({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return <span className={cn('moldy-builder-header-icon', className)}>{children}</span>
}

function BuilderRowIcon({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cn('moldy-builder-row-icon', className)}>{children}</span>
}

function BuilderTitle({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cn('moldy-builder-title', className)}>{children}</span>
}

function BuilderMessageText({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('moldy-builder-message', className)}>{children}</div>
}

function BuilderMessageBubble({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return <div className={cn('moldy-builder-message-bubble', className)}>{children}</div>
}

function BuilderAssistantName({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return <span className={cn('moldy-builder-name', className)}>{children}</span>
}

function BuilderAssistantSubtitle({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return <span className={cn('moldy-builder-subtitle', className)}>{children}</span>
}

function BuilderMuted({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cn('moldy-builder-muted', className)}>{children}</span>
}

function BuilderPhaseLabel({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return <span className={cn('moldy-builder-phase-label', className)}>{children}</span>
}

function BuilderBody({
  children,
  loose = false,
  className,
}: {
  children: ReactNode
  loose?: boolean
  className?: string
}) {
  return (
    <div className={cn(loose ? 'moldy-builder-body-loose' : 'moldy-builder-body', className)}>
      {children}
    </div>
  )
}

function BuilderSummary({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('moldy-builder-summary', className)}>{children}</div>
}

function BuilderRow({ children, className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn('moldy-builder-row', className)} {...props}>
      {children}
    </div>
  )
}

function BuilderPill({
  children,
  tone = 'default',
  className,
}: {
  children: ReactNode
  tone?: 'default' | 'primary'
  className?: string
}) {
  return (
    <span
      className={cn('moldy-builder-pill', className)}
      data-tone={tone === 'primary' ? 'primary' : undefined}
    >
      {children}
    </span>
  )
}

function BuilderTag({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cn('moldy-builder-tag', className)}>{children}</span>
}

function BuilderPath({ children, className }: { children: ReactNode; className?: string }) {
  return <code className={cn('moldy-builder-path font-mono', className)}>{children}</code>
}

function BuilderActionRow({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('moldy-builder-action-row', className)}>{children}</div>
}

function BuilderFeedbackWrap({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return <div className={cn('moldy-builder-feedback-wrap', className)}>{children}</div>
}

function BuilderTextarea({
  className,
  ...props
}: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn('moldy-builder-textarea', className)} {...props} />
}

function BuilderEditComposer({ children, className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn('moldy-builder-edit-composer', className)} {...props}>
      {children}
    </div>
  )
}

function BuilderIconButton({
  className,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button type="button" className={cn('moldy-builder-icon-button', className)} {...props}>
      {children}
    </button>
  )
}

function BuilderButton({
  tone = 'secondary',
  className,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { tone?: BuilderButtonTone }) {
  return (
    <button
      type="button"
      className={cn(
        'moldy-builder-button',
        tone === 'primary' && 'moldy-builder-button-primary',
        tone === 'secondary' && 'moldy-builder-button-secondary',
        tone === 'ghost' && 'moldy-builder-button-ghost',
        className,
      )}
      {...props}
    >
      {children}
    </button>
  )
}

export {
  BuilderActionRow,
  BuilderAssistantName,
  BuilderAssistantSubtitle,
  BuilderBody,
  BuilderButton,
  BuilderEditComposer,
  BuilderFeedbackWrap,
  BuilderHeaderIcon,
  BuilderIconButton,
  BuilderMessageBubble,
  BuilderMessageText,
  BuilderMuted,
  BuilderPath,
  BuilderPhaseLabel,
  BuilderPill,
  BuilderRow,
  BuilderRowIcon,
  BuilderSummary,
  BuilderTag,
  BuilderTextarea,
  BuilderTitle,
}
