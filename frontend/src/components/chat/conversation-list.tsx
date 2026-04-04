'use client'

import { useState, useMemo } from 'react'
import Link from 'next/link'
import { useParams, useRouter } from 'next/navigation'
import {
  PlusIcon,
  MessageSquareIcon,
  MoreVerticalIcon,
  PencilIcon,
  PinIcon,
  PinOffIcon,
  Trash2Icon,
} from 'lucide-react'
import { useTranslations, useFormatter } from 'next-intl'
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
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogCancel,
  AlertDialogAction,
} from '@/components/ui/alert-dialog'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { cn } from '@/lib/utils'
import type { Conversation } from '@/lib/types'

interface ConversationListProps {
  agentId: string
}

export function ConversationList({ agentId }: ConversationListProps) {
  const params = useParams<{ conversationId: string }>()
  const router = useRouter()
  const { data: conversations, isLoading } = useConversations(agentId)
  const createConversation = useCreateConversation(agentId)
  const updateConversation = useUpdateConversation(agentId)
  const deleteConversation = useDeleteConversation(agentId)
  const t = useTranslations('chat.conversationList')
  const tc = useTranslations('common')
  const format = useFormatter()

  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const [renameTarget, setRenameTarget] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')

  const { pinned, unpinned } = useMemo(() => {
    if (!conversations) return { pinned: [], unpinned: [] }
    return {
      pinned: conversations.filter((c) => c.is_pinned),
      unpinned: conversations.filter((c) => !c.is_pinned),
    }
  }, [conversations])

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
    return (
      <div
        key={conv.id}
        className={cn(
          'group flex items-center gap-2 rounded-lg px-3 py-2 text-sm transition-colors hover:bg-muted',
          isActive && 'bg-muted font-medium',
        )}
      >
        <Link
          href={`/agents/${agentId}/conversations/${conv.id}`}
          className="flex min-w-0 flex-1 items-center gap-2 outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
        >
          <MessageSquareIcon className="size-3.5 shrink-0 text-muted-foreground" />
          {conv.is_pinned && <PinIcon className="size-3 shrink-0 text-muted-foreground" />}
          <span className="truncate">{conv.title ?? t('fallbackTitle')}</span>
        </Link>
        <span className="shrink-0 text-xs text-muted-foreground">
          {format.dateTime(new Date(conv.updated_at), {
            month: 'numeric',
            day: 'numeric',
          })}
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
      <div className="flex items-center justify-between border-b p-3">
        <h2 className="text-sm font-medium">{t('title')}</h2>
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={handleNewConversation}
          disabled={createConversation.isPending}
        >
          <PlusIcon className="size-3.5" />
          <span className="sr-only">{t('newConversation')}</span>
        </Button>
      </div>
      <div className="flex-1 overflow-y-auto">
        {isLoading ? (
          <div className="space-y-1 p-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : conversations && conversations.length > 0 ? (
          <div className="space-y-0.5 p-2">
            {pinned.length > 0 && (
              <div className="mb-2">
                <p className="px-3 pt-1 pb-0.5 text-[0.65rem] font-medium tracking-wider text-muted-foreground">
                  {t('pinned')}
                </p>
                {pinned.map(renderItem)}
              </div>
            )}
            {unpinned.length > 0 && pinned.length > 0 && (
              <p className="px-3 pt-1 pb-0.5 text-[0.65rem] font-medium tracking-wider text-muted-foreground">
                {t('recent')}
              </p>
            )}
            {unpinned.map(renderItem)}
          </div>
        ) : (
          <div className="p-4 text-center text-xs text-muted-foreground">{t('empty')}</div>
        )}
      </div>

      <AlertDialog open={!!deleteTarget} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t('deleteDialog.title')}</AlertDialogTitle>
            <AlertDialogDescription>{t('deleteDialog.description')}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc('cancel')}</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDeleteConfirm}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/80"
            >
              {tc('delete')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <Dialog open={!!renameTarget} onOpenChange={(open) => !open && setRenameTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('renameDialog.title')}</DialogTitle>
          </DialogHeader>
          <Input
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            placeholder={t('renameDialog.placeholder')}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleRenameConfirm()
            }}
            autoFocus
          />
          <DialogFooter>
            <Button onClick={handleRenameConfirm} disabled={!renameValue.trim()}>
              {t('renameDialog.save')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
