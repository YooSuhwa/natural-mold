'use client'

import Link from 'next/link'
import { useAtomValue } from 'jotai'
import {
  LoaderCircleIcon,
  MessageSquareIcon,
  MoreVerticalIcon,
  PencilIcon,
  PinIcon,
  PinOffIcon,
  Share2Icon,
  Trash2Icon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'
import { AgentAvatar } from '@/components/agent/agent-avatar'
import type { ConversationRowActions } from '@/components/chat/use-conversation-row-actions'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import {
  conversationRuntimeStatusAtom,
  shortcutPreviewActiveAtom,
} from '@/lib/stores/chat-navigator-store'
import type { Conversation, ConversationAgentBrief } from '@/lib/types'
import { cn } from '@/lib/utils'
import { formatRelativeShort } from '@/lib/utils/format-relative-time'
import { formatShortcutLabel } from './use-chat-navigator-shortcuts'

interface ChatNavigatorSessionRowProps {
  conversation: Conversation
  agent?: ConversationAgentBrief | null
  active: boolean
  shortcutIndex?: number | null
  actions: ConversationRowActions
}

export function ChatNavigatorSessionRow({
  conversation,
  agent,
  active,
  shortcutIndex,
  actions,
}: ChatNavigatorSessionRowProps) {
  const t = useTranslations('sidebar.agents')
  const tActions = useTranslations('sidebar.agents.conversationActions')
  const tCommon = useTranslations('common')
  const runtimeStatuses = useAtomValue(conversationRuntimeStatusAtom)
  const shortcutPreviewActive = useAtomValue(shortcutPreviewActiveAtom)
  const runtimeStatus = runtimeStatuses[conversation.id] ?? conversation.runtime_status
  const isRunning = runtimeStatus === 'running'
  const href = `/agents/${conversation.agent_id}/conversations/${conversation.id}`
  const unreadCount = conversation.unread_count ?? 0

  return (
    <div
      data-chat-session-href={href}
      className={cn(
        'group/session flex h-9 items-center gap-1.5 rounded-lg px-2 text-xs transition-colors hover:bg-sidebar-accent focus-within:bg-sidebar-accent',
        active && 'bg-primary text-primary-foreground hover:bg-primary',
        unreadCount > 0 && !active && 'moldy-status-soft moldy-status-warn',
      )}
    >
      <Link
        href={href}
        className="flex min-w-0 flex-1 items-center gap-1.5 rounded outline-hidden focus-visible:ring-2 focus-visible:ring-ring"
      >
        {conversation.is_pinned ? (
          <PinIcon className="size-3 shrink-0 text-muted-foreground" />
        ) : (
          <MessageSquareIcon className="size-3.5 shrink-0 text-muted-foreground" />
        )}
        <span className="min-w-0 truncate">{conversation.title ?? t('session.fallbackTitle')}</span>
        {agent ? (
          <Tooltip>
            <TooltipTrigger
              render={
                <span className="inline-flex shrink-0 items-center group-data-[collapsible=icon]:hidden" />
              }
            >
              <AgentAvatar imageUrl={agent.image_url} name={agent.name} size="xs" />
              {/* 툴팁은 hover 전용이라 스크린리더용 이름을 DOM에 남긴다 */}
              <span className="sr-only">{agent.name}</span>
            </TooltipTrigger>
            <TooltipContent side="top">{agent.name}</TooltipContent>
          </Tooltip>
        ) : null}
      </Link>
      {unreadCount > 0 ? (
        <span className="flex h-4 min-w-4 shrink-0 items-center justify-center rounded-full bg-status-warn px-1 moldy-ui-caption font-semibold text-white">
          {unreadCount > 99 ? '99+' : unreadCount}
        </span>
      ) : null}
      <div className="flex h-7 w-14 shrink-0 items-center justify-end gap-1">
        {isRunning ? (
          <LoaderCircleIcon className="size-3.5 animate-spin text-primary-strong" />
        ) : shortcutPreviewActive && shortcutIndex && shortcutIndex <= 9 ? (
          <span className="rounded-md border border-border px-1 py-0.5 moldy-ui-caption font-semibold">
            {formatShortcutLabel(shortcutIndex)}
          </span>
        ) : (
          <span className="truncate text-right moldy-ui-caption text-muted-foreground group-hover/session:hidden group-focus-within/session:hidden">
            {formatRelativeShort(conversation.updated_at, tCommon('yesterday'))}
          </span>
        )}
        <DropdownMenu>
          <DropdownMenuTrigger
            render={<Button variant="ghost" size="icon-xs" aria-label={t('session.menu')} />}
            className={cn(
              'shrink-0 opacity-0 group-hover/session:opacity-100 focus-visible:opacity-100 data-open:opacity-100',
              isRunning && 'opacity-100',
            )}
            onClick={(event) => event.stopPropagation()}
          >
            <MoreVerticalIcon className="size-3" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" side="bottom" sideOffset={4}>
            <DropdownMenuItem onClick={() => actions.openRenameDialog(conversation)}>
              <PencilIcon />
              {tActions('menu.rename')}
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => actions.openShareDialog(conversation.id)}>
              <Share2Icon />
              {tActions('menu.share')}
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => actions.togglePin(conversation)}>
              {conversation.is_pinned ? <PinOffIcon /> : <PinIcon />}
              {conversation.is_pinned ? tActions('menu.unpin') : tActions('menu.pin')}
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              variant="destructive"
              onClick={() => actions.requestDelete(conversation)}
            >
              <Trash2Icon />
              {tActions('menu.delete')}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  )
}
