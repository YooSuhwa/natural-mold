'use client'

import { useState, useRef, useEffect, useCallback, use } from 'react'
import { useRouter } from 'next/navigation'
import {
  SendIcon,
  Loader2Icon,
  SparklesIcon,
  ArrowLeftIcon,
  RotateCcwIcon,
  WrenchIcon,
  ShieldIcon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogAction,
  AlertDialogCancel,
} from '@/components/ui/alert-dialog'
import { cn } from '@/lib/utils'
import { builderApi } from '@/lib/api/builder'
import { streamBuilder } from '@/lib/sse/stream-builder'
import type {
  BuilderDraftConfig,
  BuilderIntent,
  BuilderToolRecommendation,
  BuilderMiddlewareRecommendation,
} from '@/lib/types'

import { PhaseTimeline } from './_components/phase-timeline'
import type { PhaseState } from './_components/phase-timeline'
import { IntentCard } from './_components/intent-card'
import { RecommendationCard } from './_components/recommendation-card'
import { DraftConfigCard } from './_components/draft-config-card'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const TOTAL_PHASES = 7

function createInitialPhases(): PhaseState[] {
  return Array.from({ length: TOTAL_PHASES }, (_, i) => ({ id: i + 1, status: 'pending' as const }))
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function ConversationalCreationPage({
  searchParams,
}: {
  searchParams: Promise<{ initialMessage?: string }>
}) {
  const { initialMessage } = use(searchParams)
  const router = useRouter()
  const t = useTranslations('agent.creation')
  const tc = useTranslations('common')

  // Build state
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [buildStatus, setBuildStatus] = useState<'idle' | 'building' | 'preview' | 'failed'>('idle')
  const [isConfirming, setIsConfirming] = useState(false)
  const [showCancelConfirm, setShowCancelConfirm] = useState(false)
  const [errorMessage, setErrorMessage] = useState('')

  // Input
  const [userRequest, setUserRequest] = useState('')

  // Phase tracking
  const [phases, setPhases] = useState<PhaseState[]>(createInitialPhases)

  // Build results (populated via SSE)
  const [intent, setIntent] = useState<BuilderIntent | null>(null)
  const [tools, setTools] = useState<BuilderToolRecommendation[]>([])
  const [middlewares, setMiddlewares] = useState<BuilderMiddlewareRecommendation[]>([])
  const [draftConfig, setDraftConfig] = useState<BuilderDraftConfig | null>(null)

  const contentRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const isComposingRef = useRef(false)
  const abortRef = useRef<AbortController | null>(null)
  const hasAutoSubmitted = useRef(false)
  const buildStatusRef = useRef(buildStatus)
  buildStatusRef.current = buildStatus

  const compositionProps = {
    onCompositionStart: () => {
      isComposingRef.current = true
    },
    onCompositionEnd: () => {
      isComposingRef.current = false
    },
  } as const

  const scrollToBottom = useCallback(() => {
    if (contentRef.current) {
      contentRef.current.scrollTo({ top: contentRef.current.scrollHeight, behavior: 'smooth' })
    }
  }, [])

  // --- Phase update helpers ---

  const updatePhase = useCallback((phaseId: number, updates: Partial<PhaseState>) => {
    setPhases((prev) => prev.map((p) => (p.id === phaseId ? { ...p, ...updates } : p)))
  }, [])

  // --- Build flow (W-2: useCallback) ---

  const handleBuild = useCallback(
    async (request: string) => {
      const text = request.trim()
      if (!text) return

      setBuildStatus('building')
      setErrorMessage('')

      // Reset phases
      setPhases(createInitialPhases())
      setIntent(null)
      setTools([])
      setMiddlewares([])
      setDraftConfig(null)

      try {
        // 1. Start build session
        const session = await builderApi.start(text)
        setSessionId(session.id)

        // 2. Stream build progress
        const abort = new AbortController()
        abortRef.current = abort

        for await (const event of streamBuilder(session.id, abort.signal)) {
          switch (event.event) {
            case 'phase_progress': {
              const { phase, status, message } = event.data
              if (status === 'started') {
                updatePhase(phase, { status: 'active' })
              } else if (status === 'completed') {
                updatePhase(phase, { status: 'completed', resultSummary: message })
              } else if (status === 'warning') {
                updatePhase(phase, { status: 'warning', resultSummary: message })
              } else if (status === 'failed') {
                updatePhase(phase, { status: 'failed', resultSummary: message })
              }
              break
            }
            case 'sub_agent_start': {
              updatePhase(event.data.phase, { subAgentName: event.data.agent_name })
              break
            }
            case 'sub_agent_end': {
              updatePhase(event.data.phase, {
                subAgentName: undefined,
                resultSummary: event.data.result_summary,
              })
              break
            }
            case 'build_preview': {
              setDraftConfig(event.data.draft_config)
              setBuildStatus('preview')
              break
            }
            case 'build_failed': {
              setBuildStatus('failed')
              setErrorMessage(event.data.message)
              break
            }
            case 'error': {
              updatePhase(event.data.phase, { status: 'failed' })
              if (!event.data.recoverable) {
                setBuildStatus('failed')
                setErrorMessage(event.data.message)
              }
              break
            }
            case 'info':
            case 'stream_end':
              break
          }
        }

        // Always fetch final session to hydrate intent/tools/middlewares/draft_config
        // SSE events only carry phase progress — the actual data lives in the session
        if (buildStatusRef.current !== 'failed') {
          const finalSession = await builderApi.getSession(session.id)
          if (finalSession.intent) setIntent(finalSession.intent)
          if (finalSession.tools_result) setTools(finalSession.tools_result)
          if (finalSession.middlewares_result) setMiddlewares(finalSession.middlewares_result)
          if (finalSession.draft_config) setDraftConfig(finalSession.draft_config)
          if (finalSession.system_prompt) {
            // system_prompt is part of draft_config display
          }
          if (finalSession.status === 'preview' && buildStatusRef.current !== 'preview') {
            setBuildStatus('preview')
          }
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') return
        setBuildStatus('failed')
        setErrorMessage(err instanceof Error ? err.message : t('error.generic'))
      }
    },
    [t, updatePhase],
  )

  // Auto-submit from search params
  useEffect(() => {
    if (initialMessage && !hasAutoSubmitted.current) {
      hasAutoSubmitted.current = true
      setUserRequest(initialMessage)
      handleBuild(initialMessage)
      router.replace('/agents/new/conversational')
    }
  }, [initialMessage, handleBuild, router])

  // Focus textarea on mount
  useEffect(() => {
    if (buildStatus === 'idle') {
      textareaRef.current?.focus()
    }
  }, [buildStatus])

  // Scroll when phases update
  useEffect(() => {
    scrollToBottom()
  }, [phases, intent, tools, middlewares, draftConfig, scrollToBottom])

  const handleConfirm = useCallback(async () => {
    if (!sessionId || isConfirming) return
    setIsConfirming(true)
    try {
      const agent = await builderApi.confirm(sessionId)
      router.push(`/agents/${agent.id}`)
    } catch {
      setIsConfirming(false)
      setErrorMessage(t('error.generic'))
    }
  }, [sessionId, isConfirming, router, t])

  const handleReset = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setSessionId(null)
    setBuildStatus('idle')
    setIsConfirming(false)
    setErrorMessage('')
    setUserRequest('')
    setPhases(createInitialPhases())
    setIntent(null)
    setTools([])
    setMiddlewares([])
    setDraftConfig(null)
    hasAutoSubmitted.current = false
  }, [])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      abortRef.current?.abort()
    }
  }, [])

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b px-6 py-3">
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={t('cancelButton')}
            onClick={() => {
              if (buildStatus !== 'idle') {
                setShowCancelConfirm(true)
              } else {
                router.push('/agents/new')
              }
            }}
          >
            <ArrowLeftIcon className="size-4" />
          </Button>
          <h1 className="text-lg font-semibold">{t('header')}</h1>
        </div>
        {buildStatus !== 'idle' && (
          <Button variant="ghost" size="sm" onClick={handleReset}>
            <RotateCcwIcon className="size-4 data-[icon=inline-start]:mr-1" />
            {t('resetButton')}
          </Button>
        )}
      </div>

      {/* Scrollable content */}
      <div ref={contentRef} className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-2xl space-y-6 p-6">
          {/* Idle: Input form */}
          {buildStatus === 'idle' && (
            <div className="space-y-4">
              <div className="rounded-xl border bg-background p-5">
                <div className="flex items-start gap-2.5">
                  <SparklesIcon className="mt-0.5 size-5 shrink-0 text-primary" />
                  <p className="text-base font-semibold leading-relaxed">{t('initialQuestion')}</p>
                </div>
              </div>
              <textarea
                ref={textareaRef}
                value={userRequest}
                onChange={(e) => setUserRequest(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey && !isComposingRef.current) {
                    e.preventDefault()
                    handleBuild(userRequest)
                  }
                }}
                {...compositionProps}
                placeholder={t('initialPlaceholder')}
                rows={3}
                className={cn(
                  'min-h-[80px] max-h-[160px] w-full resize-none rounded-xl border border-input bg-transparent px-3.5 py-3 text-sm leading-relaxed outline-none transition-colors',
                  'placeholder:text-muted-foreground',
                  'focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50',
                )}
              />
              <div className="flex justify-end">
                <Button
                  onClick={() => handleBuild(userRequest)}
                  disabled={!userRequest.trim()}
                  size="lg"
                >
                  <SendIcon className="mr-1.5 size-4" />
                  {t('startButton')}
                </Button>
              </div>
            </div>
          )}

          {/* Building / Preview / Failed: Timeline + Results */}
          {buildStatus !== 'idle' && (
            <>
              {/* W-4: User request display — i18n key instead of hardcoded Korean */}
              <div className="rounded-xl border bg-primary/5 px-4 py-3">
                <p className="text-sm">
                  <span className="font-medium text-primary">{t('userRequestLabel')}</span>{' '}
                  {userRequest}
                </p>
              </div>

              {/* Phase timeline */}
              <PhaseTimeline phases={phases} />

              {/* Building spinner */}
              {buildStatus === 'building' && (
                <div className="flex flex-col items-center justify-center gap-3 py-8">
                  <Loader2Icon className="size-6 animate-spin text-primary" />
                  <p className="text-sm text-muted-foreground">{t('building')}</p>
                </div>
              )}

              {/* Error message */}
              {buildStatus === 'failed' && errorMessage && (
                <div
                  role="alert"
                  className="rounded-xl border border-destructive/50 bg-destructive/5 px-4 py-3"
                >
                  <p className="text-sm text-destructive">{errorMessage}</p>
                  <Button
                    variant="outline"
                    size="sm"
                    className="mt-2"
                    onClick={() => handleBuild(userRequest)}
                  >
                    <RotateCcwIcon className="mr-1.5 size-3.5" />
                    {t('resetButton')}
                  </Button>
                </div>
              )}

              {/* Intent card (when Phase 2 done) */}
              {intent && <IntentCard intent={intent} />}

              {tools.length > 0 && (
                <RecommendationCard
                  icon={WrenchIcon}
                  titleKey="toolRecommendation"
                  items={tools.map((tool) => ({
                    name: tool.tool_name,
                    description: tool.description,
                    reason: tool.reason,
                  }))}
                />
              )}

              {middlewares.length > 0 && (
                <RecommendationCard
                  icon={ShieldIcon}
                  titleKey="middlewareRecommendation"
                  items={middlewares.map((m) => ({
                    name: m.middleware_name,
                    description: m.description,
                    reason: m.reason,
                  }))}
                />
              )}

              {/* Draft config preview (when preview) */}
              {buildStatus === 'preview' && draftConfig && (
                <DraftConfigCard
                  draft={draftConfig}
                  onConfirm={handleConfirm}
                  isConfirming={isConfirming}
                />
              )}
            </>
          )}
        </div>
      </div>

      {/* Cancel Confirm Dialog */}
      <AlertDialog open={showCancelConfirm} onOpenChange={setShowCancelConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t('cancelConfirm')}</AlertDialogTitle>
            <AlertDialogDescription>{t('cancelDescription')}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc('cancel')}</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                abortRef.current?.abort()
                router.push('/agents/new')
              }}
            >
              {t('cancelButton')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
