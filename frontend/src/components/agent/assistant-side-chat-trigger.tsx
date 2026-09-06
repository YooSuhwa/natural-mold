'use client'

import { useRef } from 'react'
import { SparklesIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { usePathname } from 'next/navigation'

import { useAssistantSideChat } from './assistant-side-chat-provider'
import { Button } from '@/components/ui/button'
import { useAgent } from '@/lib/hooks/use-agents'

const AGENT_ROUTE_PATTERN = /^\/agents\/([^/]+)(?:\/|$)/

export function AssistantSideChatTrigger() {
  const pathname = usePathname()
  const match = pathname.match(AGENT_ROUTE_PATTERN)
  const agentId = match?.[1] === 'new' ? '' : (match?.[1] ?? '')
  const { data: agent } = useAgent(agentId)
  const { isOpen, openForAgent, target } = useAssistantSideChat()
  const triggerRef = useRef<HTMLButtonElement | null>(null)
  const t = useTranslations('agent.assistant')

  if (!agent) return null

  return (
    <Button
      ref={triggerRef}
      type="button"
      variant="ghost"
      size="icon-sm"
      className="ml-auto"
      aria-label={t('title')}
      aria-controls="assistant-side-chat"
      aria-expanded={isOpen && target?.agentId === agent.id}
      onClick={() => openForAgent({ agentId: agent.id, agentName: agent.name }, triggerRef.current)}
    >
      <SparklesIcon className="size-4" />
    </Button>
  )
}
