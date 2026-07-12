'use client'

/**
 * 평가 케이스별 grader 판정 피드백 (Phase 3 §7, D2) — 동의/비동의 + 코멘트.
 * 같은 판정을 다시 누르면 취소(삭제)된다. 표시 전용.
 */

import { useState } from 'react'
import { ThumbsDown, ThumbsUp } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import {
  useDeleteSkillCaseFeedback,
  useUpsertSkillCaseFeedback,
} from '@/lib/hooks/use-skill-evaluations'
import type { SkillCaseFeedback, SkillCaseFeedbackVerdict } from '@/lib/types/skill-evaluation'

export function SkillCaseFeedbackControls({
  skillId,
  evaluationSetId,
  runId,
  caseIndex,
  mine,
}: {
  readonly skillId: string
  readonly evaluationSetId: string
  readonly runId: string
  readonly caseIndex: number
  readonly mine: SkillCaseFeedback | null
}) {
  const t = useTranslations('skill.detailDialog.evaluation.caseFeedback')
  const upsert = useUpsertSkillCaseFeedback(skillId, evaluationSetId, runId)
  const remove = useDeleteSkillCaseFeedback(skillId, evaluationSetId, runId)
  const [comment, setComment] = useState('')
  const [commentOpen, setCommentOpen] = useState(false)
  const isPending = upsert.isPending || remove.isPending

  function toggleComment(): void {
    // Seed the editor from the saved comment when opening (view/edit, not blank).
    if (!commentOpen) setComment(mine?.comment ?? '')
    setCommentOpen((open) => !open)
  }

  function submit(verdict: SkillCaseFeedbackVerdict): void {
    if (mine?.verdict === verdict && !commentOpen) {
      remove.mutate(caseIndex)
      return
    }
    // Preserve the saved comment on a verdict-only change (agree↔disagree) —
    // only overwrite when the editor is open.
    const nextComment = commentOpen ? comment.trim() || null : (mine?.comment ?? null)
    upsert.mutate(
      {
        case_index: caseIndex,
        verdict,
        comment: nextComment,
      },
      {
        onSuccess: () => {
          setComment('')
          setCommentOpen(false)
        },
      },
    )
  }

  return (
    <div className="mt-2" data-testid={`case-feedback-${caseIndex}`}>
      <div className="flex flex-wrap items-center gap-1">
        <span className="moldy-ui-micro text-muted-foreground">{t('prompt')}</span>
        <Button
          type="button"
          size="sm"
          variant={mine?.verdict === 'agree' ? 'secondary' : 'ghost'}
          disabled={isPending}
          onClick={() => submit('agree')}
          aria-label={t('agree', { number: caseIndex + 1 })}
          data-testid={`case-feedback-agree-${caseIndex}`}
        >
          <ThumbsUp className="size-3.5" />
        </Button>
        <Button
          type="button"
          size="sm"
          variant={mine?.verdict === 'disagree' ? 'secondary' : 'ghost'}
          disabled={isPending}
          onClick={() => submit('disagree')}
          aria-label={t('disagree', { number: caseIndex + 1 })}
          data-testid={`case-feedback-disagree-${caseIndex}`}
        >
          <ThumbsDown className="size-3.5" />
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          disabled={isPending}
          onClick={toggleComment}
        >
          {commentOpen ? t('commentClose') : t('commentOpen')}
        </Button>
      </div>
      {commentOpen ? (
        <Textarea
          className="mt-1"
          value={comment}
          onChange={(event) => setComment(event.target.value)}
          placeholder={t('commentPlaceholder')}
          rows={2}
          maxLength={2000}
          data-testid={`case-feedback-comment-${caseIndex}`}
        />
      ) : null}
      {mine?.comment ? (
        <p className="mt-1 rounded-lg bg-muted/40 p-2 text-xs">{mine.comment}</p>
      ) : null}
    </div>
  )
}
