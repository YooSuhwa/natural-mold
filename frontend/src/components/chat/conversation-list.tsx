'use client'

import { useState, useMemo } from 'react'
import Link from 'next/link'
import { useParams, useRouter } from 'next/navigation'
import {
  MessageSquareIcon,
  MoreVerticalIcon,
  PencilIcon,
  PlusIcon,
  Settings2Icon,
  Share2Icon,
  PinIcon,
  PinOffIcon,
  Trash2Icon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import {
  useConversations,
  useCreateConversation,
  useUpdateConversation,
  useDeleteConversation,
} from '@/lib/hooks/use-conversations'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu'
import { DeleteConfirmDialog } from '@/components/shared/delete-confirm-dialog'
import { DialogShell } from '@/components/shared/dialog-shell'
import { ShareDialog } from '@/components/chat/share-dialog'
import { SearchInput } from '@/components/shared/search-input'
import { AgentAvatar } from '@/components/agent/agent-avatar'
import { formatRelativeShort } from '@/lib/utils/format-relative-time'
import { cn } from '@/lib/utils'
import type { Conversation } from '@/lib/types'

interface ConversationListProps {
  agentId: string
  agentName?: string
  agentImageUrl?: string | null
  agentDescription?: string | null
}

export function ConversationList({
  agentId,
  agentName,
  agentImageUrl,
  agentDescription,
}: ConversationListProps) {
  const params = useParams<{ conversationId: string }>()
  const router = useRouter()
  const { data: conversations, isLoading } = useConversations(agentId)
  const createConversation = useCreateConversation(agentId)
  const updateConversation = useUpdateConversation(agentId)
  const deleteConversation = useDeleteConversation(agentId)
  const t = useTranslations('chat.conversationList')
  const tCommon = useTranslations('common')

  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const [renameTarget, setRenameTarget] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [shareTarget, setShareTarget] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')

  // 검색어가 있으면 핀/일반 구분 없이 단일 필터, 없으면 핀 우선 정렬
  const orderedConversations = useMemo(() => {
    if (!conversations) return []
    const query = searchQuery.trim().toLowerCase()
    if (query) {
      return conversations.filter((c) =>
        (c.title ?? '').toLowerCase().includes(query),
      )
    }
    const pinned = conversations.filter((c) => c.is_pinned)
    const unpinned = conversations.filter((c) => !c.is_pinned)
    return [...pinned, ...unpinned]
  }, [conversations, searchQuery])

  const isSearching = searchQuery.trim().length > 0

  async function handleNewConversation() {
    const conv = await createConversation.mutateAsync(undefined)
    router.push(`/agents/${agentId}/conversations/${conv.id}`)
  }

  function handlePin(conv: Conversation) {
    updateConversation.mutate({ id: conv.id, data: { is_pinned: !conv.is_pinned } })
  }

  function handleDeleteConfirm() {
    if (!deleteTarget) return
    const deletingCurrent = params.conversationId === deleteTarget
    deleteConversation.mutate(deleteTarget, {
      onSuccess: () => {
        setDeleteTarget(null)
        if (deletingCurrent) {
          router.push(`/agents/${agentId}`)
        }
      },
    })
  }

  function openRenameDialog(conv: Conversation) {
    setRenameTarget(conv.id)
    setRenameValue(conv.title ?? '')
  }

  function handleRenameConfirm() {
    if (!renameTarget || !renameValue.trim()) return
    updateConversation.mutate(
      { id: renameTarget, data: { title: renameValue.trim() } },
      { onSuccess: () => setRenameTarget(null) },
    )
  }

  function renderItem(conv: Conversation) {
    const isActive = params.conversationId === conv.id
    const unreadCount = conv.unread_count ?? 0
    return (
      <div
        key={conv.id}
        className={cn(
          'group flex items-center gap-2 rounded-lg px-3 py-2 text-sm transition-colors hover:bg-muted',
          unreadCount > 0 && !isActive && 'moldy-status-soft moldy-status-warn',
          isActive &&
            'bg-primary font-medium text-primary-foreground ring-1 ring-primary-strong/20 hover:bg-primary',
        )}
      >
        <Link
          href={`/agents/${agentId}/conversations/${conv.id}`}
          className="flex min-w-0 flex-1 items-center gap-2 rounded outline-hidden focus-visible:ring-2 focus-visible:ring-ring"
        >
          {conv.is_pinned ? (
            <PinIcon className="size-3 shrink-0 text-muted-foreground" />
          ) : (
            <MessageSquareIcon className="size-3.5 shrink-0 text-muted-foreground" />
          )}
          <span className="truncate">{conv.title ?? t('fallbackTitle')}</span>
        </Link>
        {unreadCount > 0 ? (
          <span className="flex h-5 min-w-5 shrink-0 items-center justify-center rounded-full bg-status-warn px-1.5 moldy-ui-caption font-semibold text-white">
            {unreadCount > 99 ? '99+' : unreadCount}
          </span>
        ) : null}
        <span className="shrink-0 text-xs text-muted-foreground">
          {formatRelativeShort(conv.updated_at, tCommon('yesterday'))}
        </span>
        <DropdownMenu>
          <DropdownMenuTrigger
            render={<Button variant="ghost" size="icon-xs" />}
            className="shrink-0 opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
            onClick={(e) => e.stopPropagation()}
          >
            <MoreVerticalIcon className="size-3" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" side="bottom" sideOffset={4}>
            <DropdownMenuItem onClick={() => openRenameDialog(conv)}>
              <PencilIcon />
              {t('menu.rename')}
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => setShareTarget(conv.id)}>
              <Share2Icon />
              {t('menu.share')}
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => handlePin(conv)}>
              {conv.is_pinned ? <PinOffIcon /> : <PinIcon />}
              {conv.is_pinned ? t('menu.unpin') : t('menu.pin')}
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive" onClick={() => setDeleteTarget(conv.id)}>
              <Trash2Icon />
              {t('menu.delete')}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      {/* Agent card header */}
      <div className="border-b border-border/60 bg-card/55 p-4">
        <div className="flex items-start gap-3">
          <AgentAvatar imageUrl={agentImageUrl ?? null} name={agentName ?? ''} size="md" />
          <div className="flex min-w-0 flex-1 items-center gap-1">
            <h2 className="min-w-0 flex-1 truncate text-base font-semibold">
              {agentName ?? <Skeleton className="inline-block h-5 w-24" />}
            </h2>
            <Link href={`/agents/${agentId}/settings`}>
              <Button variant="ghost" size="icon-xs" aria-label={t('editAgent')}>
                <Settings2Icon className="size-3.5" />
              </Button>
            </Link>
          </div>
        </div>
        {agentDescription && (
          <p className="mt-2 line-clamp-2 text-xs leading-relaxed text-muted-foreground">
            {agentDescription}
          </p>
        )}
      </div>

      {/* "대화" 라벨 + 새 대화 버튼 */}
      <div className="flex items-center justify-between border-b border-border/60 px-4 py-2">
        <span className="text-xs font-medium text-muted-foreground">{t('label')}</span>
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={handleNewConversation}
          disabled={createConversation.isPending}
          aria-label={t('newConversation')}
        >
          <PlusIcon className="size-3.5" />
        </Button>
      </div>

      {/* 검색 입력 */}
      <div className="border-b border-border/60 px-3 py-2">
        <SearchInput
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder={t('searchPlaceholder')}
          aria-label={t('searchPlaceholder')}
          className="h-8 text-sm"
        />
      </div>

      {/* Conversation list */}
      <div className="flex-1 overflow-y-auto">
        {isLoading ? (
          <div className="space-y-1 p-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : orderedConversations.length > 0 ? (
          <div className="space-y-0.5 p-2">{orderedConversations.map(renderItem)}</div>
        ) : (
          <div className="p-4 text-center text-xs text-muted-foreground">
            {isSearching ? t('searchEmpty') : t('empty')}
          </div>
        )}
      </div>

      {/* 휴지통 풋터 (placeholder) */}
      <div className="border-t border-border/60 p-2">
        <Button
          variant="ghost"
          className="w-full justify-start gap-2 px-3 py-2 text-sm font-normal text-muted-foreground"
          onClick={() => toast.info(tCommon('comingSoon.trash'))}
        >
          <Trash2Icon className="size-4" />
          <span>{t('trash')}</span>
        </Button>
      </div>

      <DeleteConfirmDialog
        open={!!deleteTarget}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title={t('deleteDialog.title')}
        description={t('deleteDialog.description')}
        cancelLabel={tCommon('cancel')}
        confirmLabel={tCommon('delete')}
        isPending={deleteConversation.isPending}
        onConfirm={handleDeleteConfirm}
      />

      <DialogShell
        open={!!renameTarget}
        onOpenChange={(open) => !open && setRenameTarget(null)}
        size="md"
        height="auto"
      >
        <DialogShell.Header title={t('renameDialog.title')} />
        <DialogShell.Body>
          <Input
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            placeholder={t('renameDialog.placeholder')}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleRenameConfirm()
            }}
            autoFocus
          />
        </DialogShell.Body>
        <DialogShell.Footer>
          <Button onClick={handleRenameConfirm} disabled={!renameValue.trim()}>
            {t('renameDialog.save')}
          </Button>
        </DialogShell.Footer>
      </DialogShell>

      {shareTarget ? (
        <ShareDialog
          open={!!shareTarget}
          onOpenChange={(open) => !open && setShareTarget(null)}
          conversationId={shareTarget}
        />
      ) : null}
    </div>
  )
}
