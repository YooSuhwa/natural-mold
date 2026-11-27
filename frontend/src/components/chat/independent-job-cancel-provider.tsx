'use client'

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { useTranslations } from 'next-intl'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import type { IndependentChatJob } from '@/lib/types/independent-chat-job'
import { currentOrgScope, isOrgScopeCurrent } from '@/lib/org-scope/org-scope-signal'

type ScopeChoice = 'parent' | 'cascade' | 'dismiss'
type ChooseScope = (runId: string) => Promise<ScopeChoice> | undefined

const CancelScopeContext = createContext<{
  conversationId: string
  choose: ChooseScope
} | null>(null)

export function useIndependentJobCancelChoice(conversationId: string | null | undefined) {
  const context = useContext(CancelScopeContext)
  return context && context.conversationId === conversationId ? context.choose : undefined
}

export function IndependentJobCancelProvider({
  conversationId,
  jobs,
  children,
}: {
  readonly conversationId: string | null
  readonly jobs: readonly IndependentChatJob[]
  readonly children: ReactNode
}) {
  const t = useTranslations('chat.independentJobs.cancelScope')
  const [dialog, setDialog] = useState<{ conversationId: string; count: number } | null>(null)
  const pending = useRef<((choice: ScopeChoice) => void) | null>(null)
  const finish = useCallback((choice: ScopeChoice) => {
    pending.current?.(choice)
    pending.current = null
    setDialog(null)
  }, [])
  useEffect(() => () => finish('dismiss'), [conversationId, finish])

  const eligible = jobs.filter(
    (job) =>
      job.parent_conversation_id === conversationId &&
      (job.status === 'queued' ||
        job.status === 'running' ||
        job.status === 'waiting_for_approval'),
  )
  const choose: ChooseScope = (runId) => {
    const count = eligible.filter((job) => job.parent_run_id === runId).length
    if (!conversationId || count === 0) return undefined
    const org = currentOrgScope()
    pending.current?.('dismiss')
    return new Promise<ScopeChoice>((resolve) => {
      pending.current = (choice) => resolve(isOrgScopeCurrent(org) ? choice : 'dismiss')
      setDialog({ conversationId, count })
    })
  }
  return (
    <CancelScopeContext.Provider
      value={conversationId && eligible.length ? { conversationId, choose } : null}
    >
      {children}
      {dialog && dialog.conversationId === conversationId && (
        <AlertDialog
          open
          onOpenChange={(open) => {
            if (!open) finish('dismiss')
          }}
        >
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>{t('title')}</AlertDialogTitle>
              <AlertDialogDescription>
                {t('description', { count: dialog.count })}
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter className="flex-wrap">
              <AlertDialogCancel>{t('dismiss')}</AlertDialogCancel>
              <AlertDialogAction variant="outline" onClick={() => finish('parent')}>
                {t('parentOnly')}
              </AlertDialogAction>
              <AlertDialogAction variant="destructive" onClick={() => finish('cascade')}>
                {t('withChildren')}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      )}
    </CancelScopeContext.Provider>
  )
}
