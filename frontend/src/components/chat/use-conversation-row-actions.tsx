'use client'

import { useState, type ReactNode } from 'react'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { useMutation, useQueryClient, type QueryClient, type QueryKey } from '@tanstack/react-query'
import { DeleteConfirmDialog } from '@/components/shared/delete-confirm-dialog'
import { DialogShell } from '@/components/shared/dialog-shell'
import { ShareDialog } from '@/components/chat/share-dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { conversationsApi } from '@/lib/api/conversations'
import { conversationKeys, invalidateConversationNavigators } from '@/lib/hooks/use-conversations'
import type { Conversation, ConversationUpdateRequest } from '@/lib/types'

interface ConversationRowActionsOptions {
  activeConversationId?: string | null
  translationNamespace?: 'chat.conversationList' | 'sidebar.agents.conversationActions'
}

export interface ConversationRowActions {
  isDeleting: boolean
  dialogs: ReactNode
  openRenameDialog: (conversation: Conversation) => void
  openShareDialog: (conversationId: string) => void
  requestDelete: (conversation: Conversation) => void
  togglePin: (conversation: Conversation) => void
}

interface NavigatorPages {
  pages: { items: Conversation[] }[]
}

function isNavigatorPages(data: unknown): data is NavigatorPages {
  return typeof data === 'object' && data !== null && Array.isArray((data as NavigatorPages).pages)
}

function navigatorCacheFilters(agentId: string) {
  // list prefix가 agent page 쿼리까지 포섭하고, ['conversations','page']가 글로벌 쿼리를 잡는다
  return [
    { queryKey: conversationKeys.list(agentId) },
    { queryKey: ['conversations', 'page'] as QueryKey },
  ]
}

/** 핀/제목 변경을 내비게이터 캐시(목록·infinite page)에 즉시 반영하고 롤백 스냅샷을 돌려준다. */
function patchConversationCaches(
  queryClient: QueryClient,
  conversation: Conversation,
  patch: ConversationUpdateRequest,
): Array<[QueryKey, unknown]> {
  const patchItem = (item: Conversation) =>
    item.id === conversation.id ? { ...item, ...patch } : item
  const snapshots: Array<[QueryKey, unknown]> = []
  for (const filter of navigatorCacheFilters(conversation.agent_id)) {
    snapshots.push(...queryClient.getQueriesData(filter))
    queryClient.setQueriesData(filter, (data: unknown) => {
      if (Array.isArray(data)) return (data as Conversation[]).map(patchItem)
      if (isNavigatorPages(data)) {
        return {
          ...data,
          pages: data.pages.map((page) => ({ ...page, items: page.items.map(patchItem) })),
        }
      }
      return data
    })
  }
  return snapshots
}

export function useConversationRowActions({
  activeConversationId,
  translationNamespace = 'chat.conversationList',
}: ConversationRowActionsOptions): ConversationRowActions {
  const router = useRouter()
  const queryClient = useQueryClient()
  const updateConversation = useMutation({
    mutationFn: ({
      conversation,
      data,
    }: {
      conversation: Conversation
      data: ConversationUpdateRequest
    }) => conversationsApi.update(conversation.id, data),
    onMutate: async ({ conversation, data }) => {
      // 진행 중인 refetch가 낙관 패치를 덮어쓰지 않도록 먼저 취소한다
      await Promise.all(
        navigatorCacheFilters(conversation.agent_id).map((filter) =>
          queryClient.cancelQueries(filter),
        ),
      )
      return { snapshots: patchConversationCaches(queryClient, conversation, data) }
    },
    onError: (_error, _variables, context) => {
      context?.snapshots.forEach(([queryKey, data]) => queryClient.setQueryData(queryKey, data))
    },
    onSettled: (_updated, _error, variables) =>
      invalidateConversationNavigators(
        queryClient,
        variables.conversation.agent_id,
        variables.conversation.id,
      ),
  })
  const deleteConversation = useMutation({
    mutationFn: (conversation: Conversation) => conversationsApi.delete(conversation.id),
    onSuccess: (_deleted, conversation) =>
      invalidateConversationNavigators(queryClient, conversation.agent_id),
  })
  const t = useTranslations(translationNamespace)
  const tCommon = useTranslations('common')
  const [deleteTarget, setDeleteTarget] = useState<Conversation | null>(null)
  const [renameTarget, setRenameTarget] = useState<Conversation | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [shareTarget, setShareTarget] = useState<string | null>(null)

  function togglePin(conversation: Conversation) {
    updateConversation.mutate({
      conversation,
      data: { is_pinned: !conversation.is_pinned },
    })
  }

  function openRenameDialog(conversation: Conversation) {
    setRenameTarget(conversation)
    setRenameValue(conversation.title ?? '')
  }

  function handleRenameConfirm() {
    if (!renameTarget || !renameValue.trim()) return
    updateConversation.mutate(
      { conversation: renameTarget, data: { title: renameValue.trim() } },
      { onSuccess: () => setRenameTarget(null) },
    )
  }

  function handleDeleteConfirm() {
    if (!deleteTarget) return
    const target = deleteTarget
    const deletingCurrent = activeConversationId === target.id
    deleteConversation.mutate(target, {
      onSuccess: () => {
        setDeleteTarget(null)
        // hook의 agentId는 글로벌 목록에서 다른 에이전트 대화일 수 있다 — 대상 기준으로 이동
        if (deletingCurrent) router.push(`/agents/${target.agent_id}`)
      },
    })
  }

  return {
    isDeleting: deleteConversation.isPending,
    openRenameDialog,
    openShareDialog: setShareTarget,
    requestDelete: setDeleteTarget,
    togglePin,
    dialogs: (
      <>
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
              onChange={(event) => setRenameValue(event.target.value)}
              placeholder={t('renameDialog.placeholder')}
              onKeyDown={(event) => {
                if (event.key === 'Enter') handleRenameConfirm()
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
      </>
    ),
  }
}
