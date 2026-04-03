'use client'

import { useState, useRef, useEffect, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  SendIcon,
  Loader2Icon,
  CheckIcon,
  CircleDotIcon,
  ClockIcon,
  SparklesIcon,
  MessageCircleIcon,
  WrenchIcon,
  XIcon,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { MarkdownContent } from '@/components/chat/markdown-content'
import { cn } from '@/lib/utils'
import { creationSessionApi, type CreationMessageResult } from '@/lib/api/creation-session'
import type { DraftConfig } from '@/lib/types'

const PHASES = [
  { id: 1, label: '프로젝트 초기화', description: '요청 분석' },
  { id: 2, label: '사용자 의도 분석', description: '의도 수집' },
  { id: 3, label: '도구 추천', description: '도구 선택' },
  { id: 4, label: '에이전트 생성', description: '최종 구성' },
] as const

type SuggestedReplies = NonNullable<CreationMessageResult['suggested_replies']>
type RecommendedTool = CreationMessageResult['recommended_tools'][number]
interface PhaseLog {
  phase: number
  result: string
}

// --- Phase Timeline ---
function PhaseTimeline({ currentPhase }: { currentPhase: number }) {
  return (
    <div className="rounded-xl border bg-muted/30 p-4">
      <h3 className="mb-3 text-sm font-medium text-muted-foreground">진행 상황</h3>
      <div className="space-y-0">
        {PHASES.map((phase, idx) => {
          const status =
            phase.id < currentPhase ? 'completed' : phase.id === currentPhase ? 'active' : 'pending'
          const isLast = idx === PHASES.length - 1
          return (
            <div key={phase.id} className="flex gap-3">
              <div className="flex flex-col items-center">
                {status === 'completed' ? (
                  <div className="flex size-6 items-center justify-center rounded-full bg-emerald-500 text-white">
                    <CheckIcon className="size-3.5" />
                  </div>
                ) : status === 'active' ? (
                  <div className="flex size-6 items-center justify-center rounded-full bg-primary text-primary-foreground">
                    <CircleDotIcon className="size-3.5" />
                  </div>
                ) : (
                  <div className="flex size-6 items-center justify-center rounded-full border-2 border-muted-foreground/30 text-muted-foreground/50">
                    <ClockIcon className="size-3" />
                  </div>
                )}
                {!isLast && (
                  <div
                    className={cn(
                      'w-0.5 min-h-4 flex-1',
                      status === 'completed' ? 'bg-emerald-500' : 'bg-muted-foreground/20',
                    )}
                  />
                )}
              </div>
              <div className="flex flex-1 items-start justify-between pb-4">
                <p
                  className={cn(
                    'text-sm leading-6',
                    status === 'active'
                      ? 'font-semibold text-foreground'
                      : status === 'completed'
                        ? 'font-medium text-foreground'
                        : 'text-muted-foreground',
                  )}
                >
                  Phase {phase.id}: {phase.label}
                  <span className="ml-1.5 font-normal text-muted-foreground">
                    {phase.description}
                  </span>
                </p>
                <span
                  className={cn(
                    'shrink-0 rounded-md px-2 py-0.5 text-xs font-medium',
                    status === 'completed'
                      ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-400'
                      : status === 'active'
                        ? 'bg-primary/10 text-primary'
                        : 'bg-muted text-muted-foreground',
                  )}
                >
                  {status === 'completed' ? '완료' : status === 'active' ? '진행중' : '대기중'}
                </span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// --- Option Card ---
function OptionCard({
  label,
  selected,
  multiSelect,
  onClick,
}: {
  label: string
  selected: boolean
  multiSelect: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'flex w-full cursor-pointer items-center gap-3 rounded-xl border px-4 py-3.5 text-left text-sm transition-all active:scale-[0.99]',
        selected
          ? 'border-primary bg-primary/5 ring-1 ring-primary/30'
          : 'border-border bg-background hover:border-primary/30 hover:bg-muted/50',
      )}
    >
      <div
        className={cn(
          'flex size-5 shrink-0 items-center justify-center rounded-full border-2 transition-colors',
          selected
            ? 'border-primary bg-primary text-primary-foreground'
            : 'border-muted-foreground/40',
          multiSelect && 'rounded-md',
        )}
      >
        {selected && <CheckIcon className="size-3" />}
      </div>
      <span className={cn('flex-1', selected && 'font-medium')}>{label}</span>
    </button>
  )
}

// --- Tool Card ---
function ToolCard({ tool }: { tool: RecommendedTool }) {
  return (
    <div className="flex gap-3 rounded-xl border bg-background p-4">
      <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
        <WrenchIcon className="size-4" />
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold">{tool.name}</p>
        <p className="mt-0.5 text-sm text-muted-foreground leading-relaxed">{tool.description}</p>
      </div>
    </div>
  )
}

// --- Main Page ---
export default function ConversationalCreationPage() {
  const router = useRouter()
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [currentPhase, setCurrentPhase] = useState(1)
  const [isLoading, setIsLoading] = useState(false)
  const [isConfirming, setIsConfirming] = useState(false)

  // Phase 1: Initial request
  const [initialInput, setInitialInput] = useState('')

  // Phase 2: Question flow
  const [question, setQuestion] = useState('')
  const [contextText, setContextText] = useState('')
  const [suggestions, setSuggestions] = useState<SuggestedReplies | null>(null)
  const [selectedOptions, setSelectedOptions] = useState<Set<string>>(new Set())
  const [showCustomInput, setShowCustomInput] = useState(false)
  const [customInput, setCustomInput] = useState('')

  // Phase 3: Tool recommendation
  const [recommendedTools, setRecommendedTools] = useState<RecommendedTool[]>([])
  const [modificationInput, setModificationInput] = useState('')

  // Phase 4: Final
  const [draftConfig, setDraftConfig] = useState<DraftConfig | null>(null)

  // Logs
  const [phaseLogs, setPhaseLogs] = useState<PhaseLog[]>([])

  const contentRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const initialTextareaRef = useRef<HTMLTextAreaElement>(null)
  const isComposingRef = useRef(false)

  const compositionProps = {
    onCompositionStart: () => {
      isComposingRef.current = true
    },
    onCompositionEnd: () => {
      isComposingRef.current = false
    },
  } as const

  const scrollToTop = useCallback(() => {
    contentRef.current?.scrollTo({ top: 0, behavior: 'smooth' })
  }, [])

  // Start session on mount
  useEffect(() => {
    let cancelled = false
    async function startSession() {
      try {
        const session = await creationSessionApi.start()
        if (cancelled) return
        setSessionId(session.id)
      } catch {
        setQuestion('세션을 시작하는 데 문제가 발생했습니다. 페이지를 새로고침해주세요.')
      }
    }
    startSession()
    return () => {
      cancelled = true
    }
  }, [])

  // Focus initial textarea
  useEffect(() => {
    if (sessionId && currentPhase === 1) {
      initialTextareaRef.current?.focus()
    }
  }, [sessionId, currentPhase])

  function applyResponse(response: CreationMessageResult) {
    setCurrentPhase(response.current_phase)
    setQuestion(response.question ?? '')
    setContextText(response.content)

    if (response.phase_result) {
      setPhaseLogs((prev) => [
        ...prev,
        {
          phase: response.current_phase - 1,
          result: response.phase_result!,
        },
      ])
    }

    if (response.suggested_replies && response.suggested_replies.options.length > 0) {
      setSuggestions(response.suggested_replies)
    } else {
      setSuggestions(null)
    }

    if (response.recommended_tools.length > 0) {
      setRecommendedTools(response.recommended_tools)
    } else {
      setRecommendedTools([])
    }

    if (response.draft_config) {
      setDraftConfig(response.draft_config)
    }

    setSelectedOptions(new Set())
    setShowCustomInput(false)
    setCustomInput('')
    setModificationInput('')
    scrollToTop()
  }

  // Phase 1: Send initial request
  async function handleSubmit(text: string) {
    if (!text || !sessionId || isLoading) return

    setIsLoading(true)
    try {
      const response = await creationSessionApi.sendMessage(sessionId, text)
      applyResponse(response)
    } catch {
      setQuestion('오류가 발생했습니다. 다시 시도해주세요.')
      setSuggestions(null)
    } finally {
      setIsLoading(false)
    }
  }

  function handleOptionClick(option: string) {
    if (option === '직접 입력') {
      setShowCustomInput(true)
      setSelectedOptions(new Set())
      return
    }

    setShowCustomInput(false)

    if (!suggestions?.multi_select) {
      // Single select: replace selection
      setSelectedOptions(new Set([option]))
      return
    }

    // Multi select: toggle
    setSelectedOptions((prev) => {
      const next = new Set(prev)
      if (next.has(option)) next.delete(option)
      else next.add(option)
      return next
    })
  }

  function handleSelectionSubmit() {
    if (selectedOptions.size === 0) return
    handleSubmit(Array.from(selectedOptions).join(', '))
  }

  function handleCustomSubmit() {
    const text = customInput.trim()
    if (!text) return
    handleSubmit(text)
  }

  // Phase 3: Approve tools
  function handleApproveTools() {
    handleSubmit('승인')
  }

  function handleRequestModification() {
    const text = modificationInput.trim()
    if (!text) return
    handleSubmit(`수정 요청: ${text}`)
  }

  // Phase 4: Confirm creation
  async function handleConfirm() {
    if (!sessionId || isConfirming) return
    setIsConfirming(true)
    try {
      const agent = await creationSessionApi.confirm(sessionId)
      router.push(`/agents/${agent.id}`)
    } catch {
      setIsConfirming(false)
    }
  }

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {/* Header */}
      <div className="border-b px-6 py-3">
        <h1 className="text-lg font-semibold">에이전트 만들기</h1>
      </div>

      {/* Scrollable content */}
      <div ref={contentRef} className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-2xl space-y-6 p-6">
          {/* Phase 1: Initial request input */}
          {currentPhase === 1 && !isLoading && (
            <div className="space-y-4">
              <div className="rounded-xl border bg-background p-5">
                <div className="flex items-start gap-2.5">
                  <MessageCircleIcon className="mt-0.5 size-5 shrink-0 text-muted-foreground" />
                  <p className="text-base font-semibold leading-relaxed">
                    어떤 에이전트를 만들고 싶으세요?
                  </p>
                </div>
              </div>
              <textarea
                ref={initialTextareaRef}
                value={initialInput}
                onChange={(e) => setInitialInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey && !isComposingRef.current) {
                    e.preventDefault()
                    handleSubmit(initialInput.trim())
                  }
                }}
                {...compositionProps}
                placeholder='예: "한글과컴퓨터 관련 뉴스를 매일 요약해주는 에이전트"'
                rows={3}
                className={cn(
                  'min-h-[80px] max-h-[160px] w-full resize-none rounded-xl border border-input bg-transparent px-3.5 py-3 text-sm leading-relaxed outline-none transition-colors',
                  'placeholder:text-muted-foreground',
                  'focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50',
                )}
              />
              <div className="flex justify-end">
                <Button
                  onClick={() => handleSubmit(initialInput.trim())}
                  disabled={!initialInput.trim() || !sessionId}
                  size="lg"
                >
                  <SendIcon className="mr-1.5 size-4" />
                  시작
                </Button>
              </div>
            </div>
          )}

          {/* Phase logs */}
          {phaseLogs.length > 0 && (
            <div className="space-y-3">
              {phaseLogs.map((log, i) => (
                <div key={i} className="rounded-xl border bg-muted/20 px-4 py-3">
                  <p className="text-xs font-medium text-emerald-600 dark:text-emerald-400">
                    [Phase {log.phase} 완료]
                  </p>
                  <p className="mt-1 text-sm">{log.result}</p>
                </div>
              ))}
            </div>
          )}

          {/* Phase Timeline (show after Phase 1) */}
          {currentPhase > 1 && <PhaseTimeline currentPhase={currentPhase} />}

          {/* Loading */}
          {isLoading && (
            <div className="flex items-center justify-center py-12">
              <Loader2Icon className="size-6 animate-spin text-muted-foreground" />
            </div>
          )}

          {/* Phase 2: Question + Options */}
          {currentPhase === 2 && !isLoading && (question || contextText) && (
            <div className="space-y-4">
              {/* Context text (outside the question card, muted) */}
              {contextText && (
                <p className="text-sm text-muted-foreground leading-relaxed">{contextText}</p>
              )}

              {/* Question card */}
              {question && (
                <div className="rounded-xl border bg-background p-5">
                  <div className="flex items-start gap-2.5">
                    <MessageCircleIcon className="mt-0.5 size-5 shrink-0 text-muted-foreground" />
                    <p className="text-base font-semibold leading-relaxed">{question}</p>
                  </div>
                </div>
              )}

              {/* Option cards */}
              {suggestions && suggestions.options.length > 0 && (
                <div className="space-y-2">
                  {suggestions.multi_select && (
                    <p className="text-xs text-muted-foreground">여러 개 선택할 수 있어요</p>
                  )}
                  {suggestions.options.map((option) => (
                    <OptionCard
                      key={option}
                      label={option}
                      selected={
                        option === '직접 입력' ? showCustomInput : selectedOptions.has(option)
                      }
                      multiSelect={suggestions.multi_select}
                      onClick={() => handleOptionClick(option)}
                    />
                  ))}
                </div>
              )}

              {/* Custom input */}
              {showCustomInput && (
                <textarea
                  ref={textareaRef}
                  value={customInput}
                  onChange={(e) => setCustomInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey && !isComposingRef.current) {
                      e.preventDefault()
                      handleCustomSubmit()
                    }
                  }}
                  {...compositionProps}
                  placeholder="원하는 내용을 자유롭게 작성하세요..."
                  rows={2}
                  className={cn(
                    'min-h-[60px] max-h-[160px] w-full resize-none rounded-xl border border-input bg-transparent px-3.5 py-2.5 text-sm leading-relaxed outline-none transition-colors',
                    'placeholder:text-muted-foreground',
                    'focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50',
                  )}
                />
              )}

              {/* Fallback: no suggestions from AI */}
              {!suggestions && !showCustomInput && (
                <textarea
                  value={customInput}
                  onChange={(e) => setCustomInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey && !isComposingRef.current) {
                      e.preventDefault()
                      handleCustomSubmit()
                    }
                  }}
                  {...compositionProps}
                  placeholder="답변을 입력하세요..."
                  rows={2}
                  className={cn(
                    'min-h-[60px] max-h-[160px] w-full resize-none rounded-xl border border-input bg-transparent px-3.5 py-2.5 text-sm leading-relaxed outline-none transition-colors',
                    'placeholder:text-muted-foreground',
                    'focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50',
                  )}
                />
              )}

              {/* Submit button — inline below options */}
              {(selectedOptions.size > 0 ||
                showCustomInput ||
                (!suggestions && customInput.trim())) && (
                <div className="flex justify-end">
                  <Button
                    onClick={
                      showCustomInput || !suggestions ? handleCustomSubmit : handleSelectionSubmit
                    }
                    disabled={
                      showCustomInput || !suggestions
                        ? !customInput.trim()
                        : selectedOptions.size === 0
                    }
                    size="lg"
                  >
                    <SendIcon className="mr-1.5 size-4" />
                    제출
                  </Button>
                </div>
              )}
            </div>
          )}

          {/* Phase 3: Tool recommendation */}
          {currentPhase === 3 && !isLoading && (
            <div className="space-y-4">
              {contextText && (
                <p className="text-sm text-muted-foreground leading-relaxed">{contextText}</p>
              )}

              {recommendedTools.length > 0 && (
                <Card>
                  <CardHeader>
                    <CardTitle className="text-base">도구 추천</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {recommendedTools.map((tool) => (
                      <ToolCard key={tool.name} tool={tool} />
                    ))}
                  </CardContent>
                </Card>
              )}

              {/* Modification input + buttons */}
              <textarea
                value={modificationInput}
                onChange={(e) => setModificationInput(e.target.value)}
                onKeyDown={(e) => {
                  if (
                    e.key === 'Enter' &&
                    !e.shiftKey &&
                    !isComposingRef.current &&
                    modificationInput.trim()
                  ) {
                    e.preventDefault()
                    handleRequestModification()
                  }
                }}
                {...compositionProps}
                placeholder="수정 의견을 입력하세요..."
                rows={2}
                className={cn(
                  'min-h-[60px] max-h-[120px] w-full resize-none rounded-xl border border-input bg-transparent px-3.5 py-2.5 text-sm leading-relaxed outline-none transition-colors',
                  'placeholder:text-muted-foreground',
                  'focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50',
                )}
              />
              <div className="flex justify-end gap-2">
                {modificationInput.trim() && (
                  <Button variant="outline" onClick={handleRequestModification} size="lg">
                    <XIcon className="mr-1.5 size-4" />
                    수정요청
                  </Button>
                )}
                <Button onClick={handleApproveTools} size="lg">
                  <CheckIcon className="mr-1.5 size-4" />
                  승인
                </Button>
              </div>
            </div>
          )}

          {/* Phase 4: Final result */}
          {currentPhase === 4 && !isLoading && draftConfig?.is_ready && (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                  <SparklesIcon className="size-4 text-primary" />
                  에이전트 구성 완료
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                {contextText && (
                  <div className="text-sm leading-relaxed">
                    <MarkdownContent content={contextText} />
                  </div>
                )}

                {/* Agent info */}
                <div className="space-y-2.5 rounded-lg bg-muted/50 p-4 text-sm">
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">이름</span>
                    <span className="font-medium">{draftConfig.name ?? '-'}</span>
                  </div>
                  {draftConfig.description && (
                    <div className="flex justify-between gap-4">
                      <span className="shrink-0 text-muted-foreground">설명</span>
                      <span className="text-right">{draftConfig.description}</span>
                    </div>
                  )}
                  {draftConfig.recommended_model && (
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">모델</span>
                      <span>{draftConfig.recommended_model}</span>
                    </div>
                  )}
                </div>

                {/* Tools */}
                {draftConfig.recommended_tool_names &&
                  draftConfig.recommended_tool_names.length > 0 && (
                    <div className="space-y-2">
                      <h4 className="text-sm font-medium">
                        포함된 도구 ({draftConfig.recommended_tool_names.length}
                        개)
                      </h4>
                      <div className="space-y-1.5">
                        {draftConfig.recommended_tool_names.map((name) => (
                          <div
                            key={name}
                            className="flex items-center gap-2 rounded-lg bg-muted/50 px-3 py-2 text-sm"
                          >
                            <WrenchIcon className="size-3.5 text-muted-foreground" />
                            {name}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                {/* System prompt */}
                {draftConfig.system_prompt && (
                  <details>
                    <summary className="cursor-pointer text-sm text-muted-foreground hover:text-foreground">
                      시스템 프롬프트 보기
                    </summary>
                    <pre className="mt-2 max-h-48 overflow-auto rounded-lg bg-muted p-3 text-xs whitespace-pre-wrap">
                      {draftConfig.system_prompt}
                    </pre>
                  </details>
                )}

                <Button
                  onClick={handleConfirm}
                  disabled={isConfirming}
                  className="w-full"
                  size="lg"
                >
                  {isConfirming && <Loader2Icon className="mr-1.5 size-4 animate-spin" />}
                  에이전트 생성
                </Button>
              </CardContent>
            </Card>
          )}
        </div>
      </div>

      {/* intentionally no bottom bar — submit buttons are inline with content */}
    </div>
  )
}
