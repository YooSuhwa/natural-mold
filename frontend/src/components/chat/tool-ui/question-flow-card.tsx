'use client'

import { useCallback, useMemo, useState } from 'react'
import { ChevronLeftIcon, CheckIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import {
  serializeQuestionFlowResponse,
  type SerializedUserInputResponse,
} from '@/lib/chat/decision-mappers'
import type { UserInputOption, UserInputQuestion } from '@/lib/types'

type AnswersByQuestion = Record<string, string[]>

interface QuestionFlowCardProps {
  id: string
  title?: string
  questions: UserInputQuestion[]
  submitting: boolean
  onInteract: () => void
  onSubmit: (response: SerializedUserInputResponse) => void | Promise<void>
}

function optionId(option: UserInputOption): string {
  return option.id ?? option.label
}

function questionId(question: UserInputQuestion, index: number): string {
  return question.id ?? question.label ?? question.question ?? `question_${index + 1}`
}

function questionLabel(question: UserInputQuestion, index: number): string {
  return question.label ?? question.question ?? question.id ?? `Question ${index + 1}`
}

function normalizedOptions(question: UserInputQuestion): UserInputOption[] {
  return question.options ?? []
}

export function QuestionFlowCard({
  id,
  title,
  questions,
  submitting,
  onInteract,
  onSubmit,
}: QuestionFlowCardProps) {
  const t = useTranslations('chat.userInput')
  const [currentIndex, setCurrentIndex] = useState(0)
  const [answers, setAnswers] = useState<AnswersByQuestion>({})

  const total = questions.length
  const currentQuestion = questions[currentIndex]
  const currentId = currentQuestion ? questionId(currentQuestion, currentIndex) : ''
  const selected = useMemo(() => new Set(answers[currentId] ?? []), [answers, currentId])
  const isLast = currentIndex === total - 1
  const currentOptions = currentQuestion ? normalizedOptions(currentQuestion) : []
  const currentType =
    currentQuestion?.type ?? (currentOptions.length > 0 ? 'single_select' : 'text')
  const required = currentQuestion?.required ?? true
  const currentText = answers[currentId]?.[0] ?? ''
  const canProceed =
    !required || (currentType === 'text' ? currentText.trim() !== '' : selected.size > 0)

  const updateAnswer = useCallback(
    (questionKey: string, values: string[]) => {
      setAnswers((prev) => ({ ...prev, [questionKey]: values }))
      onInteract()
    },
    [onInteract],
  )

  const toggleOption = useCallback(
    (option: UserInputOption) => {
      if (!currentQuestion || option.disabled) return
      const oid = optionId(option)
      if (currentType === 'single_select') {
        updateAnswer(currentId, selected.has(oid) ? [] : [oid])
        return
      }
      const next = new Set(selected)
      if (next.has(oid)) next.delete(oid)
      else next.add(oid)
      updateAnswer(currentId, Array.from(next))
    },
    [currentId, currentQuestion, currentType, selected, updateAnswer],
  )

  const goBack = useCallback(() => {
    setCurrentIndex((idx) => Math.max(0, idx - 1))
    onInteract()
  }, [onInteract])

  const goNext = useCallback(() => {
    if (!canProceed) return
    if (!isLast) {
      setCurrentIndex((idx) => Math.min(total - 1, idx + 1))
      onInteract()
      return
    }
    void onSubmit(serializeQuestionFlowResponse(questions, answers))
  }, [answers, canProceed, isLast, onInteract, onSubmit, questions, total])

  if (!currentQuestion) return null

  return (
    <div className="space-y-4" data-tool-ui-id={id}>
      <div className="space-y-2">
        {title && <p className="text-sm font-semibold">{title}</p>}
        <div
          className="flex h-1.5 gap-1"
          role="progressbar"
          aria-valuemin={1}
          aria-valuemax={total}
          aria-valuenow={currentIndex + 1}
        >
          {questions.map((question, idx) => (
            <div
              key={questionId(question, idx)}
              className="h-full flex-1 overflow-hidden rounded-full bg-muted"
            >
              <div
                className={cn(
                  'h-full rounded-full bg-primary-strong transition-transform duration-200',
                  idx <= currentIndex ? 'scale-x-100' : 'scale-x-0',
                )}
              />
            </div>
          ))}
        </div>
        <p className="text-xs font-medium uppercase text-muted-foreground">
          {t('stepStatus', { current: currentIndex + 1, total })}
        </p>
      </div>

      <div className="space-y-3">
        <div>
          <p className="text-sm font-semibold">{questionLabel(currentQuestion, currentIndex)}</p>
          {currentQuestion.question &&
            currentQuestion.label &&
            currentQuestion.question !== currentQuestion.label && (
              <p className="mt-0.5 text-xs text-muted-foreground">{currentQuestion.question}</p>
            )}
        </div>

        {currentType === 'text' ? (
          <textarea
            value={currentText}
            onChange={(event) => updateAnswer(currentId, [event.target.value])}
            onFocus={onInteract}
            placeholder={t('placeholder')}
            className="w-full resize-none rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none transition-colors placeholder:text-muted-foreground focus:border-primary/50 focus:ring-1 focus:ring-primary/30"
            rows={3}
          />
        ) : (
          <div
            className="space-y-1.5"
            role="listbox"
            aria-multiselectable={currentType === 'multi_select'}
          >
            {currentOptions.map((option) => {
              const oid = optionId(option)
              const checked = selected.has(oid)
              return (
                <button
                  key={oid}
                  type="button"
                  disabled={option.disabled}
                  onClick={() => toggleOption(option)}
                  className={cn(
                    'flex min-h-11 w-full items-start gap-3 rounded-lg border px-3 py-2 text-left text-sm transition-colors',
                    checked
                      ? 'border-primary/60 bg-primary/10 text-primary-strong'
                      : 'border-border hover:border-primary/50 hover:bg-accent',
                    option.disabled && 'cursor-not-allowed opacity-50',
                  )}
                  role="option"
                  aria-selected={checked}
                >
                  <span
                    className={cn(
                      'mt-0.5 flex size-4 shrink-0 items-center justify-center border-2',
                      currentType === 'single_select' ? 'rounded-full' : 'rounded',
                      checked
                        ? 'border-primary-strong bg-primary-strong text-white'
                        : 'border-muted-foreground/50',
                    )}
                  >
                    {currentType === 'single_select'
                      ? checked && <span className="size-1.5 rounded-full bg-current" />
                      : checked && <CheckIcon className="size-3" />}
                  </span>
                  <span className="min-w-0">
                    <span className="block font-medium leading-5">{option.label}</span>
                    {option.description && (
                      <span className="block text-xs leading-5 text-muted-foreground">
                        {option.description}
                      </span>
                    )}
                  </span>
                </button>
              )
            })}
          </div>
        )}
      </div>

      <div className="flex items-center justify-between pt-1">
        <button
          type="button"
          onClick={goBack}
          disabled={currentIndex === 0 || submitting}
          className={cn(
            'inline-flex items-center gap-1 rounded-full px-3 py-2 text-xs font-medium transition-colors',
            currentIndex === 0 || submitting
              ? 'cursor-not-allowed text-muted-foreground/50'
              : 'text-muted-foreground hover:bg-muted hover:text-foreground',
          )}
        >
          <ChevronLeftIcon className="size-3.5" />
          {t('back')}
        </button>
        <button
          type="button"
          onClick={goNext}
          disabled={!canProceed || submitting}
          className={cn(
            'rounded-full px-4 py-2 text-xs font-medium transition-colors',
            canProceed && !submitting
              ? 'bg-primary text-primary-foreground hover:bg-primary/90'
              : 'cursor-not-allowed bg-muted text-muted-foreground',
          )}
        >
          {submitting ? t('sending') : isLast ? t('complete') : t('next')}
        </button>
      </div>
    </div>
  )
}
