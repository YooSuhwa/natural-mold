'use client'

import { useCallback, useState } from 'react'
import Link from 'next/link'
import { useQueryClient } from '@tanstack/react-query'
import { ArrowLeftIcon, FolderOpenIcon, HammerIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { ChatRuntimeSection } from '@/components/chat/chat-runtime-section'
import { Badge } from '@/components/ui/badge'
import { buttonVariants } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useSession } from '@/lib/auth/session'
import { skillQueryKeys } from '@/lib/query-keys/skills'
import { cn } from '@/lib/utils'
import { moldyAttachmentAdapter } from '@/lib/chat/attachment-adapter'
import { conversationKeys, useMessagesEnvelope } from '@/lib/hooks/use-conversations'
import { skillBuilderKeys, useSkillBuilderSession } from '@/lib/hooks/use-skill-builder'
import type { Message, SSEEvent } from '@/lib/types'
import { SkillBuilderAutoRequest, resolveAutoFirstMessage } from './skill-builder-auto-request'
import { SkillBuilderRail, type RailMode } from './skill-builder-rail'
import { SkillBuilderTryHint } from './skill-builder-try-hint'

const EMPTY_MESSAGES: Message[] = []

/**
 * 빌더는 v3(langgraph) 전용 — `useLangGraphRuntime`을 항상 켜므로 legacy
 * SSE 경로는 도달 불가. prop 계약 충족용 스텁.
 */
async function* builderLegacyStreamUnsupported(): AsyncGenerator<SSEEvent> {
  throw new Error('skill builder chat requires the langgraph_v3 runtime')
}

export function SkillBuilderChatClient({ sessionId }: { readonly sessionId: string }) {
  const t = useTranslations('skill.builderChat')
  const queryClient = useQueryClient()
  const { data: user } = useSession()
  const { data: session, isLoading, isError } = useSkillBuilderSession(sessionId)
  const [railMode, setRailMode] = useState<RailMode>('status')

  const conversationId = session?.conversation_id ?? null
  const agentId = session?.agent_id ?? null
  const { data: envelope } = useMessagesEnvelope(conversationId ?? '', Boolean(conversationId))
  const messages = envelope?.messages ?? EMPTY_MESSAGES
  // Phase 1.5 — 다이얼로그의 user_request를 첫 진입 시 자동 전송 (리로드/기존
  // run 이력에는 발화하지 않는 서버 진실 가드).
  const autoRequestText = resolveAutoFirstMessage(session, envelope)

  const handleStreamEnd = useCallback(() => {
    // finalize/validate가 세션 상태·skills 목록을 바꿀 수 있다 — 런 종료 시 재조회.
    if (conversationId) {
      queryClient.invalidateQueries({ queryKey: conversationKeys.messages(conversationId) })
    }
    queryClient.invalidateQueries({ queryKey: skillBuilderKeys.detail(sessionId) })
    queryClient.invalidateQueries({ queryKey: skillBuilderKeys.files(sessionId) })
    queryClient.invalidateQueries({ queryKey: skillQueryKeys.all })
  }, [conversationId, queryClient, sessionId])

  // React Compiler가 자동 메모이즈 — 수동 useMemo는 컴파일러 추론과 충돌한다.
  const emptyContent = (
    <div className="mx-auto max-w-xl space-y-3 py-10 text-center">
      <HammerIcon className="mx-auto size-8 text-muted-foreground" />
      <h2 className="text-base font-semibold">{t('emptyTitle')}</h2>
      <p className="text-sm text-muted-foreground">{t('emptyHint')}</p>
      {session?.user_request ? (
        <p className="moldy-muted-panel mx-auto max-w-md px-3 py-2 text-sm text-muted-foreground">
          {session.user_request}
        </p>
      ) : null}
    </div>
  )

  if (isLoading) {
    return (
      <div className="moldy-app-surface flex min-h-0 flex-1 gap-3 overflow-hidden p-3">
        <section className="moldy-panel flex-1 space-y-4 p-6">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </section>
      </div>
    )
  }

  if (isError || !session || !conversationId || !agentId) {
    return (
      <div className="moldy-app-surface flex min-h-0 flex-1 items-center justify-center p-3">
        <div className="moldy-panel max-w-md space-y-3 p-6 text-center">
          <h2 className="text-base font-semibold">{t('sessionUnavailableTitle')}</h2>
          <p className="text-sm text-muted-foreground">{t('sessionUnavailableHint')}</p>
          <Link href="/skills" className={cn(buttonVariants({ variant: 'outline' }))}>
            <ArrowLeftIcon className="size-4" />
            {t('backToSkills')}
          </Link>
        </div>
      </div>
    )
  }

  return (
    <div className="moldy-app-surface flex min-h-0 flex-1 gap-3 overflow-hidden p-3">
      <section className="moldy-panel flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <header className="flex items-center gap-2 border-b border-border/60 px-4 py-3">
          <Link
            href="/skills"
            aria-label={t('backToSkills')}
            className={cn(buttonVariants({ variant: 'ghost', size: 'sm' }))}
          >
            <ArrowLeftIcon className="size-4" />
          </Link>
          <HammerIcon className="size-4 shrink-0 text-muted-foreground" />
          <h1 className="shrink-0 text-sm font-semibold">{t('title')}</h1>
          <Badge variant="outline" className="shrink-0">
            {session.mode === 'improve' ? t('modeImprove') : t('modeCreate')}
          </Badge>
          {/* 목업 헤더 헬퍼 — 모드별 안내 문구 */}
          <span className="hidden min-w-0 truncate text-xs text-muted-foreground lg:inline">
            {session.mode === 'improve' ? t('helperImprove') : t('helperCreate')}
          </span>
          <span className="ml-auto flex shrink-0 items-center gap-2">
            <button
              type="button"
              data-testid="builder-open-source"
              onClick={() => setRailMode(railMode === 'source' ? 'status' : 'source')}
              className={cn(
                buttonVariants({ variant: 'outline', size: 'sm' }),
                'hidden md:inline-flex',
              )}
            >
              <FolderOpenIcon className="size-3.5" />
              {railMode === 'source' ? t('closeSource') : t('viewSource')}
            </button>
            <Badge variant="secondary" data-testid="builder-session-status">
              {t(`status.${session.status}` as Parameters<typeof t>[0])}
            </Badge>
          </span>
        </header>

        <ChatRuntimeSection
          activeConversationId={conversationId}
          activeRun={envelope?.active_run ?? null}
          agentId={agentId}
          agentName={t('title')}
          attachmentAdapter={moldyAttachmentAdapter}
          composerHint={
            <>
              <SkillBuilderAutoRequest text={autoRequestText} />
              <SkillBuilderTryHint />
            </>
          }
          emptyContent={emptyContent}
          latestRun={envelope?.latest_run ?? null}
          messages={messages}
          onRuntimeStatusChange={() => undefined}
          onStreamEnd={handleStreamEnd}
          streamFn={builderLegacyStreamUnsupported}
          useLangGraphRuntime
          user={user}
        />
      </section>

      <SkillBuilderRail
        conversationId={conversationId}
        session={session}
        mode={railMode}
        onModeChange={setRailMode}
      />
    </div>
  )
}
