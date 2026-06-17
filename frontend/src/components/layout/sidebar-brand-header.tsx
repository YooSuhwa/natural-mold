'use client'

import { ToggleLeftIcon, ToggleRightIcon } from 'lucide-react'
import Image from 'next/image'
import Link from 'next/link'

import { SidebarHeader, useSidebar } from '@/components/ui/sidebar'

type SidebarBrandHeaderProps = {
  readonly brandLabel: string
  readonly homeHref: string
  readonly toggleLabel: string
}

export function SidebarBrandHeader({ brandLabel, homeHref, toggleLabel }: SidebarBrandHeaderProps) {
  const { toggleSidebar } = useSidebar()

  return (
    <SidebarHeader className="px-4 py-4 group-data-[collapsible=icon]:px-0 group-data-[collapsible=icon]:py-2">
      <div className="flex items-center justify-between group-data-[collapsible=icon]:hidden">
        <Link href={homeHref} aria-label={brandLabel} className="flex items-center gap-2">
          <Image
            src="/logo.webp"
            alt={brandLabel}
            width={32}
            height={32}
            className="size-8 shrink-0 object-contain"
          />
          <span className="text-lg font-bold">{brandLabel}</span>
        </Link>
        <button
          type="button"
          onClick={toggleSidebar}
          className="moldy-sidebar-control rounded-lg border border-transparent p-1.5 text-primary-strong hover:border-sidebar-border"
          aria-label={toggleLabel}
        >
          <ToggleRightIcon className="size-5" />
        </button>
      </div>
      <div className="hidden items-center justify-center group-data-[collapsible=icon]:flex">
        <button
          type="button"
          onClick={toggleSidebar}
          className="group/toggle relative flex size-8 items-center justify-center rounded-lg transition-colors hover:bg-sidebar-accent"
          aria-label={toggleLabel}
        >
          <Image
            src="/logo.webp"
            alt={brandLabel}
            width={32}
            height={32}
            className="size-8 object-contain transition-opacity group-hover/toggle:opacity-0"
          />
          <ToggleLeftIcon className="absolute size-5 text-muted-foreground opacity-0 transition-opacity group-hover/toggle:opacity-100" />
        </button>
      </div>
    </SidebarHeader>
  )
}
