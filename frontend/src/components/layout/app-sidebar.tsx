"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import {
  HomeIcon,
  WrenchIcon,
  CpuIcon,
  BarChart3Icon,
  PlusIcon,
  BotIcon,
  UserIcon,
  SettingsIcon,
  LogOutIcon,
  ChevronsUpDownIcon,
} from "lucide-react"

import { useAgents } from "@/lib/hooks/use-agents"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarSeparator,
} from "@/components/ui/sidebar"
import { Skeleton } from "@/components/ui/skeleton"
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu"

const navItems = [
  { label: "홈", href: "/", icon: HomeIcon },
  { label: "도구", href: "/tools", icon: WrenchIcon },
  { label: "모델", href: "/models", icon: CpuIcon },
  { label: "사용량", href: "/usage", icon: BarChart3Icon },
]

export function AppSidebar() {
  const pathname = usePathname()
  const { data: agents, isLoading } = useAgents()

  const recentAgents = agents
    ?.slice()
    .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
    .slice(0, 5)

  return (
    <Sidebar>
      <SidebarHeader className="px-4 py-4">
        <Link href="/" className="flex items-center gap-2">
          <div className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground font-bold text-sm">
            M
          </div>
          <span className="text-lg font-bold tracking-tight">Moldy</span>
        </Link>
      </SidebarHeader>

      <SidebarContent>
        {/* New Agent Button */}
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton
                  variant="outline"
                  tooltip="새 에이전트"
                  render={<Link href="/agents/new" />}
                >
                  <PlusIcon className="size-4" />
                  <span>새 에이전트</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        <SidebarSeparator />

        {/* Main Navigation */}
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              {navItems.map((item) => {
                const isActive =
                  item.href === "/"
                    ? pathname === "/"
                    : pathname.startsWith(item.href)
                return (
                  <SidebarMenuItem key={item.href}>
                    <SidebarMenuButton
                      isActive={isActive}
                      tooltip={item.label}
                      render={<Link href={item.href} />}
                    >
                      <item.icon className="size-4" />
                      <span>{item.label}</span>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                )
              })}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        {/* Recent Agents */}
        {(isLoading || (recentAgents && recentAgents.length > 0)) && (
          <>
            <SidebarSeparator />
            <SidebarGroup>
              <SidebarGroupLabel>최근 에이전트</SidebarGroupLabel>
              <SidebarGroupContent>
                <SidebarMenu>
                  {isLoading ? (
                    Array.from({ length: 3 }).map((_, i) => (
                      <li key={i} className="flex h-8 items-center gap-2 rounded-md px-2">
                        <Skeleton className="size-4 rounded-md" />
                        <Skeleton className="h-4 flex-1" />
                      </li>
                    ))
                  ) : (
                    recentAgents?.map((agent) => {
                      const isActive = pathname === `/agents/${agent.id}` || pathname.startsWith(`/agents/${agent.id}/`)
                      return (
                        <SidebarMenuItem key={agent.id}>
                          <SidebarMenuButton
                            isActive={isActive}
                            tooltip={agent.name}
                            render={<Link href={`/agents/${agent.id}`} />}
                          >
                            <BotIcon className="size-4" />
                            <span>{agent.name}</span>
                          </SidebarMenuButton>
                        </SidebarMenuItem>
                      )
                    })
                  )}
                </SidebarMenu>
              </SidebarGroupContent>
            </SidebarGroup>
          </>
        )}
      </SidebarContent>

      {/* User Profile Footer */}
      <SidebarFooter>
        <SidebarSeparator />
        <SidebarMenu>
          <SidebarMenuItem>
            <DropdownMenu>
              <DropdownMenuTrigger
                className="w-full rounded-md ring-sidebar-ring outline-hidden focus-visible:ring-2 [&>svg]:size-4 [&>svg]:shrink-0"
                render={
                  <SidebarMenuButton
                    size="lg"
                    className="data-open:bg-sidebar-accent data-open:text-sidebar-accent-foreground"
                  />
                }
              >
                <div className="flex size-8 items-center justify-center rounded-lg bg-sidebar-accent">
                  <UserIcon className="size-4 text-sidebar-accent-foreground" />
                </div>
                <div className="grid flex-1 text-left text-sm leading-tight">
                  <span className="truncate font-medium">수화</span>
                  <span className="truncate text-xs text-muted-foreground">
                    suhwa@moldy.ai
                  </span>
                </div>
                <ChevronsUpDownIcon className="ml-auto size-4" />
              </DropdownMenuTrigger>
              <DropdownMenuContent
                side="top"
                align="start"
                className="w-[--anchor-width]"
              >
                <DropdownMenuItem>
                  <SettingsIcon />
                  환경설정
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem>
                  <LogOutIcon />
                  로그아웃
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
    </Sidebar>
  )
}
