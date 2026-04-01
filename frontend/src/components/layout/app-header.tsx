"use client"

import { UserIcon } from "lucide-react"
import { SidebarTrigger } from "@/components/ui/sidebar"
import { Separator } from "@/components/ui/separator"

export function AppHeader() {
  return (
    <header className="flex h-12 shrink-0 items-center gap-2 border-b px-4">
      <SidebarTrigger />
      <Separator orientation="vertical" className="mx-1 h-4" />
      <div className="flex-1" />
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <UserIcon className="size-4" />
        <span>사용자</span>
      </div>
    </header>
  )
}
