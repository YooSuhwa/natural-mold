'use client'

import { useState } from 'react'
import { useAuiState } from '@assistant-ui/react'
import { ArrowLeftToLineIcon, CheckIcon, SaveIcon, XIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { useSaveSideChat } from '@/lib/hooks/use-side-chat'
import { useSideChat } from './side-chat-context'

export function SideChatToolbar({ agentId }: { readonly agentId: string }) {
  const workspace = useSideChat()
  const t = useTranslations('chat.sideChat')
  const { mutateAsync: saveSideChat } = useSaveSideChat(agentId)
  const messages = useAuiState((state) => state.thread.messages)
  const running = useAuiState((state) => state.thread.isRunning)
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved'>('idle')
  const answer = messages.findLast(
    (message) =>
      message.role === 'assistant' && message.content.some((part) => part.type === 'text'),
  )
  const answerText =
    answer?.content
      .filter((part) => part.type === 'text')
      .map((part) => part.text)
      .join('\n') ?? ''
  const save = async () => {
    if (!workspace?.sideId) return
    setSaveStatus('saving')
    try {
      await saveSideChat(workspace.sideId)
      setSaveStatus('saved')
      toast.success(t('saved'))
    } catch {
      setSaveStatus('idle')
      toast.error(t('saveFailed'))
    }
  }
  const addAnswer = () => {
    if (!workspace?.sideId || !answer) return
    if (answerText.length > 8000) {
      toast.info(t('selectShorter'))
      return
    }
    workspace.deliver({
      target: 'main',
      reference: {
        kind: 'conversation',
        id: workspace.sideId,
        message_id: answer.id,
        message_role: 'assistant',
        quote: answerText,
        label: t('title'),
      },
    })
    // On a narrow viewport the dialog must release focus to the main composer.
    if (window.matchMedia('(max-width: 1279px)').matches) workspace.close()
  }
  return (
    <header className="flex shrink-0 flex-col gap-1 border-b border-border/60 px-4 py-3">
      <div className="flex min-w-0 items-center justify-between gap-2 pr-6 xl:pr-0">
        <h2 className="truncate text-sm font-medium">{t('title')}</h2>
        <div className="flex items-center gap-1">
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label={t(saveStatus === 'saved' ? 'saved' : 'saveConversation')}
            disabled={saveStatus !== 'idle'}
            onClick={() => void save()}
          >
            {saveStatus === 'saved' ? (
              <CheckIcon className="size-4" />
            ) : (
              <SaveIcon className="size-4" />
            )}
          </Button>
          <Button
            size="icon-sm"
            variant="ghost"
            className="hidden xl:inline-flex"
            aria-label={t('close')}
            onClick={workspace?.close}
          >
            <XIcon className="size-4" />
          </Button>
        </div>
      </div>
      <div className="flex items-center justify-between gap-2">
        <p className="min-w-0 truncate text-xs text-muted-foreground">{workspace?.mainTitle}</p>
        <Button size="sm" variant="ghost" disabled={!answerText || running} onClick={addAnswer}>
          <ArrowLeftToLineIcon className="size-3.5" />
          {t('addToChat')}
        </Button>
      </div>
    </header>
  )
}
