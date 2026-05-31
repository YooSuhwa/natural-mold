/**
 * 표준 ``Decision`` 객체 빌더 — HiTL ResumeRequest payload 의 4 액션 타입을
 * 인라인 리터럴 대신 일관된 helper 로 생성. 호출처에서 type 누락이나 잘못된
 * 필드 조합(예: respond 에 edited_action 첨부)을 컴파일 시점에 차단.
 *
 * ADR-012 §Decision schema 와 1:1 대응. Decision shape 변경 시 본 파일 한 곳
 * 만 수정하면 호출처 일괄 적용.
 */
import type { Decision } from '@/lib/types'
import type { UserInputOption, UserInputQuestion } from '@/lib/types'

export function toApprove(): Decision {
  return { type: 'approve' }
}

export function toReject(message?: string): Decision {
  return message !== undefined ? { type: 'reject', message } : { type: 'reject' }
}

export function toEdit(editedAction: NonNullable<Decision['edited_action']>): Decision {
  return { type: 'edit', edited_action: editedAction }
}

export function toRespond(message: string): Decision {
  return { type: 'respond', message }
}

export type SerializedUserInputResponse = {
  message: string
  displayText: string
}

type QuestionFlowAnswers = Record<string, string[] | string | null | undefined>

function optionId(option: UserInputOption): string {
  return option.id ?? option.label
}

function optionLabelById(options: UserInputOption[] | undefined, id: string): string {
  return options?.find((option) => optionId(option) === id)?.label ?? id
}

function questionId(question: UserInputQuestion, index: number): string {
  return question.id ?? question.label ?? question.question ?? `question_${index + 1}`
}

function questionLabel(question: UserInputQuestion, index: number): string {
  return question.label ?? question.question ?? question.id ?? `Question ${index + 1}`
}

function normalizeAnswer(value: string[] | string | null | undefined): string[] {
  if (Array.isArray(value)) return value
  if (typeof value === 'string' && value) return [value]
  return []
}

export function serializeQuestionFlowResponse(
  questions: UserInputQuestion[],
  answers: QuestionFlowAnswers,
): SerializedUserInputResponse & {
  summary: Array<{ id: string; label: string; value: string }>
} {
  const answerIds: Record<string, string[]> = {}
  const labels: Record<string, string | string[]> = {}
  const summary: Array<{ id: string; label: string; value: string }> = []

  questions.forEach((question, index) => {
    const id = questionId(question, index)
    const selected = normalizeAnswer(answers[id])
    const selectedLabels = selected.map((value) =>
      question.type === 'text' ? value : optionLabelById(question.options, value),
    )
    answerIds[id] = selected
    labels[id] = question.type === 'multi_select' ? selectedLabels : (selectedLabels[0] ?? '')
    summary.push({
      id,
      label: questionLabel(question, index),
      value: selectedLabels.join(', '),
    })
  })

  return {
    message: JSON.stringify({
      mode: 'question_flow',
      answers: answerIds,
      labels,
    }),
    displayText: summary.map((item) => `${item.label}: ${item.value}`).join(' | '),
    summary,
  }
}

export function serializeOptionListResponse(
  options: UserInputOption[],
  selection: string[] | string | null | undefined,
): SerializedUserInputResponse {
  const selected = normalizeAnswer(selection)
  const labels = selected.map((id) => optionLabelById(options, id))
  return {
    message: JSON.stringify({
      mode: 'option_list',
      selection: selected,
      labels,
    }),
    displayText: labels.join(', '),
  }
}
