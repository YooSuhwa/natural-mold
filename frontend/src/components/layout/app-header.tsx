"use client"

import { UserIcon } from "lucide-react"
import { SidebarTrigger } from "@/components/ui/sidebar"
import { Separator } from "@/components/ui/separator"

export function AppHeader() {
  return (
    <header className="flex h-12 shrink-0 items-center gap-2 border-b px-4">
      <SidebarTrigger aria-label="사이드바 열기/닫기" className="cursor-pointer" />
      <Separator orientation="vertical" className="mx-1 h-4" />
      <div className="flex-1" />
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <div className="flex size-6 items-center justify-center rounded-full bg-primary/10">
          <UserIcon className="size-3.5 text-primary" />
        </div>
        <span>Demo User</span>
      </div>
    </header>
  )
}
