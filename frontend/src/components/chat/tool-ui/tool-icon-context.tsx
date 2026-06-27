'use client'

import { createContext, useContext, type ReactNode } from 'react'
import { WrenchIcon, type LucideIcon } from 'lucide-react'
import { builtinToolIcon } from '@/lib/chat/tool-icons'
import { getDomainIcon } from '@/components/shared/icon'

// ──────────────────────────────────────────────
// ToolIconContext — toolName → 도구 registry icon_id.
//
// 채팅 루트(conversations 페이지)가 agent.tools에서 만든 map을 주입한다. 도구 pill은
// useToolIcon으로 아이콘을 정한다: 빌트인 고정 맵(런타임 주입 도구) → agent.tools의
// icon_id(사용자 registry 도구) → 렌치 폴백. map이 없는 표면(빈 컨텍스트)에서도
// 빌트인 맵 + 렌치로 graceful하게 동작한다.
// ──────────────────────────────────────────────

const ToolIconIdContext = createContext<Readonly<Record<string, string>>>({})

export function ToolIconProvider({
  iconIds,
  children,
}: {
  iconIds: Readonly<Record<string, string>>
  children: ReactNode
}) {
  return <ToolIconIdContext.Provider value={iconIds}>{children}</ToolIconIdContext.Provider>
}

/** toolName → leading 아이콘. 빌트인 맵 → 도구 icon_id → 렌치 폴백. */
export function useToolIcon(toolName: string): LucideIcon {
  const iconIds = useContext(ToolIconIdContext)
  const builtin = builtinToolIcon(toolName)
  if (builtin) return builtin
  const iconId = iconIds[toolName]
  if (iconId) return getDomainIcon(iconId)
  return WrenchIcon
}
