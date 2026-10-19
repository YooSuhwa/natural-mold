'use client'

import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

interface SettingsShellProps {
  children: ReactNode
  wide?: boolean
  className?: string
}

export function SettingsShell({ children, wide = false, className }: SettingsShellProps) {
  return (
    // The scroll container is a tab stop so keyboard users can scroll a page whose content
    // has no focusable element (axe scrollable-region-focusable, e.g. a read-only status page).
    <div
      tabIndex={0}
      className="flex flex-1 flex-col overflow-auto p-6 outline-hidden focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
    >
      <div className={cn('mx-auto w-full min-w-0', wide ? 'max-w-7xl' : 'max-w-5xl', className)}>
        {children}
      </div>
    </div>
  )
}
