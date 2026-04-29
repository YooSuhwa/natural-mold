'use client'

import {
  Box,
  Plug,
  Code,
  Globe,
  Search,
  Mail,
  Calendar,
  MessageSquare,
  Sparkles,
  KeyRound,
  Server,
  Database,
  Wrench,
  FileText,
  Lock,
  Cloud,
} from 'lucide-react'
import type { ComponentType, SVGProps } from 'react'
import { cn } from '@/lib/utils'

type IconComponent = ComponentType<SVGProps<SVGSVGElement>>

// Icon registry — credential/tool definition `icon_id` → Lucide icon. We never
// ship third-party brand assets; if a definition needs a brand mark, the
// backend supplies a generic `icon_id` and we map to a category-appropriate
// Lucide glyph.
const ICONS: Record<string, IconComponent> = {
  // generic
  box: Box,
  plug: Plug,
  code: Code,
  globe: Globe,
  cloud: Cloud,
  database: Database,
  wrench: Wrench,
  server: Server,
  file: FileText,
  lock: Lock,
  key: KeyRound,
  sparkles: Sparkles,
  // categories
  search: Search,
  mail: Mail,
  email: Mail,
  calendar: Calendar,
  chat: MessageSquare,
  message: MessageSquare,
  // common credential definitions
  openai: Sparkles,
  anthropic: Sparkles,
  google_genai: Sparkles,
  azure_openai: Sparkles,
  google_workspace_oauth2: Mail,
  gmail: Mail,
  google_calendar: Calendar,
  google_chat: MessageSquare,
  google_search: Search,
  naver_search: Search,
  http_bearer: KeyRound,
  http_api_key: KeyRound,
  http_basic: Lock,
  mcp_oauth2: Server,
  http_request: Globe,
}

interface DomainIconProps {
  iconId?: string | null
  fallback?: keyof typeof ICONS
  className?: string
  size?: number
}

export function DomainIcon({
  iconId,
  fallback = 'box',
  className,
  size,
}: DomainIconProps) {
  const Icon: IconComponent =
    (iconId ? ICONS[iconId] : undefined) ?? ICONS[fallback] ?? Box
  return (
    <Icon
      className={cn('size-4 text-foreground/80', className)}
      width={size}
      height={size}
      aria-hidden
    />
  )
}
