'use client'

import { createContext, useContext, type ReactNode } from 'react'
import { PlugIcon, WrenchIcon, type LucideIcon } from 'lucide-react'
import { builtinToolIcon } from '@/lib/chat/tool-icons'
import { getDomainIcon } from '@/components/shared/icon'

// ──────────────────────────────────────────────
// ToolIconContext — toolName → 도구 registry icon_id / MCP 서버명.
//
// 채팅 루트(conversations 페이지)가 agent.tools/agent.mcp_tools에서 만든 map을
// 주입한다. 도구 pill은 useToolIcon으로 아이콘을 정한다: 빌트인 고정 맵(런타임
// 주입 도구) → agent.tools의 icon_id(사용자 registry 도구) → MCP 도구는 플러그
// 아이콘 → 렌치 폴백. useMcpToolServer는 pill 메타의 서버 배지에 쓰인다.
// map이 없는 표면(빈 컨텍스트)에서도 빌트인 맵 + 렌치로 graceful하게 동작한다.
// ──────────────────────────────────────────────

const ToolIconIdContext = createContext<Readonly<Record<string, string>>>({})
const McpToolServerContext = createContext<Readonly<Record<string, string>>>({})

export function ToolIconProvider({
  iconIds,
  mcpServers = {},
  children,
}: {
  iconIds: Readonly<Record<string, string>>
  mcpServers?: Readonly<Record<string, string>>
  children: ReactNode
}) {
  return (
    <ToolIconIdContext.Provider value={iconIds}>
      <McpToolServerContext.Provider value={mcpServers}>{children}</McpToolServerContext.Provider>
    </ToolIconIdContext.Provider>
  )
}

/** toolName → leading 아이콘. 빌트인 맵 → 도구 icon_id → MCP 플러그 → 렌치 폴백. */
export function useToolIcon(toolName: string): LucideIcon {
  const iconIds = useContext(ToolIconIdContext)
  const mcpServers = useContext(McpToolServerContext)
  const builtin = builtinToolIcon(toolName)
  if (builtin) return builtin
  const iconId = iconIds[toolName]
  if (iconId) return getDomainIcon(iconId)
  if (toolName in mcpServers) return PlugIcon
  return WrenchIcon
}

/** toolName이 현재 에이전트의 MCP 도구면 그 서버 표시명, 아니면 null. */
export function useMcpToolServer(toolName: string): string | null {
  const mcpServers = useContext(McpToolServerContext)
  return mcpServers[toolName] ?? null
}
