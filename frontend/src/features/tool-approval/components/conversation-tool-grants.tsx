'use client'

import { useParams } from 'next/navigation'
import { ShieldCheckIcon, XIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { useChatConversationId } from '@/components/chat/conversation-context'
import { ApiError } from '@/lib/api/client'
import {
  useToolApproval,
  useToolGrantMutations,
  useToolGrants,
} from '@/lib/hooks/use-tool-approval'

function useAgentId(): string | undefined {
  const params = useParams<{ agentId?: string }>()
  return typeof params?.agentId === 'string' ? params.agentId : undefined
}

/**
 * "이 대화 동안 허용" on an approval card (approval-modes-plan Q5): shown only for a tool the
 * company cap and the agent's settings let a conversation lift. It does not decide this
 * call; later calls of the tool in this conversation run without the card.
 */
export function ConversationGrantButton({ tool }: { tool: string | undefined }) {
  const agentId = useAgentId()
  const conversationId = useChatConversationId() ?? undefined
  // Outside a conversation page (tests, previews) there is nothing to allow.
  if (!agentId || !conversationId || !tool) return null
  return <GrantButton agentId={agentId} conversationId={conversationId} tool={tool} />
}

function GrantButton({
  agentId,
  conversationId,
  tool,
}: {
  agentId: string
  conversationId: string
  tool: string
}) {
  const t = useTranslations('toolApproval.grant')
  const view = useToolApproval(agentId)
  const grants = useToolGrants(conversationId)
  const { grant } = useToolGrantMutations(conversationId)
  const row = view.data?.tools.find((item) => item.name === tool)
  if (!row?.grantable) return null
  if (grants.data?.some((item) => item.tool_name === tool)) return null
  return (
    <button
      type="button"
      className="text-xs text-primary-strong underline disabled:opacity-50"
      disabled={grant.isPending}
      onClick={() =>
        grant.mutate(tool, {
          onSuccess: () => toast.success(t('done', { tool })),
          onError: (error) => toast.error(error instanceof ApiError ? error.message : t('failed')),
        })
      }
    >
      {t('button')}
    </button>
  )
}

/** The tools allowed for this conversation, each revocable (above the thread). */
export function ConversationToolGrantChips() {
  const conversationId = useChatConversationId()
  return conversationId ? <GrantChips conversationId={conversationId} /> : null
}

function GrantChips({ conversationId }: { conversationId: string }) {
  const t = useTranslations('toolApproval.grant')
  const grants = useToolGrants(conversationId)
  const { revoke } = useToolGrantMutations(conversationId)
  const items = grants.data ?? []
  if (items.length === 0) return null
  return (
    <div
      className="flex flex-wrap items-center gap-2 border-b px-4 py-1.5 text-xs"
      role="region"
      aria-label={t('chipsLabel')}
    >
      <ShieldCheckIcon className="size-3.5 text-muted-foreground" aria-hidden />
      <span className="text-muted-foreground">{t('chipsLabel')}</span>
      {items.map((item) => (
        <span
          key={item.id}
          className="moldy-status-surface moldy-status-info flex items-center gap-1 rounded-full px-2 py-0.5"
        >
          <span className="font-mono">{item.tool_name}</span>
          <button
            type="button"
            className="rounded-full hover:bg-accent"
            aria-label={t('revoke', { tool: item.tool_name })}
            disabled={revoke.isPending}
            onClick={() => revoke.mutate(item.tool_name)}
          >
            <XIcon className="size-3" aria-hidden />
          </button>
        </span>
      ))}
    </div>
  )
}
