"use client"

import { SidebarTrigger } from "@/components/ui/sidebar"
import { Separator } from "@/components/ui/separator"

export function AppHeader() {
  return (
    <header className="flex h-12 shrink-0 items-center gap-2 border-b px-4">
      <SidebarTrigger aria-label="사이드바 열기/닫기" className="cursor-pointer" />
      <Separator orientation="vertical" className="mx-1 h-4" />
      <div className="flex-1" />
    </header>
  )
}
