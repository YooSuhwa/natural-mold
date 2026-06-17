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
    <div className="flex flex-1 flex-col overflow-auto p-6">
      <main className={cn('mx-auto w-full min-w-0', wide ? 'max-w-7xl' : 'max-w-5xl', className)}>
        {children}
      </main>
    </div>
  )
}
