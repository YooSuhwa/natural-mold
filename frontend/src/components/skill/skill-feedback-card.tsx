'use client'

/**
 * 스킬 단위 휴먼 피드백 카드 (Phase 3 §7, D2) — up/down + 코멘트.
 * 표시 전용: 통과율/health 계산에 반영되지 않는다.
 */

import { useState } from 'react'
import { ThumbsDown, ThumbsUp } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import {
  useDeleteSkillFeedback,
  useSkillFeedback,
  useUpsertSkillFeedback,
} from '@/lib/hooks/use-skill-feedback'
import type { SkillFeedbackRating } from '@/lib/types/skill-feedback'

export function SkillFeedbackCard({ skillId }: { readonly skillId: string }) {
  const t = useTranslations('skill.detailDialog.evaluation.feedback')
  const { data: summary, isLoading } = useSkillFeedback(skillId)
  const upsert = useUpsertSkillFeedback(skillId)
  const remove = useDeleteSkillFeedback(skillId)
  const [comment, setComment] = useState('')
  const [commentOpen, setCommentOpen] = useState(false)

  if (isLoading) {
    return <Skeleton className="h-24 w-full rounded-lg" />
  }
  if (!summary) return null

  const mine = summary.mine ?? null
  const isPending = upsert.isPending || remove.isPending

  function toggleComment(): void {
    // Seed the editor from the saved comment when opening so it can be viewed
    // and edited (never a blank box over an existing comment).
    if (!commentOpen) setComment(mine?.comment ?? '')
    setCommentOpen((open) => !open)
  }

  function submit(rating: SkillFeedbackRating): void {
    if (mine?.rating === rating && !commentOpen) {
      remove.mutate()
      return
    }
    // Preserve the saved comment on a rating-only change — only overwrite it
    // when the editor is open (else switching up↔down silently wipes it).
    const nextComment = commentOpen ? comment.trim() || null : (mine?.comment ?? null)
    upsert.mutate(
      { rating, comment: nextComment },
      {
        onSuccess: () => {
          setComment('')
          setCommentOpen(false)
        },
      },
    )
  }

  return (
    <section className="rounded-lg border border-border/70 p-3" data-testid="skill-feedback-card">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">{t('title')}</h3>
        <span className="moldy-ui-micro text-muted-foreground">{t('displayOnly')}</span>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button
          type="button"
          size="sm"
          variant={mine?.rating === 'up' ? 'secondary' : 'outline'}
          disabled={isPending}
          onClick={() => submit('up')}
          aria-label={t('rateUp')}
          data-testid="skill-feedback-up"
        >
          <ThumbsUp className="size-4" />
          <span className="tabular-nums">{summary.up_count}</span>
        </Button>
        <Button
          type="button"
          size="sm"
          variant={mine?.rating === 'down' ? 'secondary' : 'outline'}
          disabled={isPending}
          onClick={() => submit('down')}
          aria-label={t('rateDown')}
          data-testid="skill-feedback-down"
        >
          <ThumbsDown className="size-4" />
          <span className="tabular-nums">{summary.down_count}</span>
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
        <div className="mt-2 space-y-2">
          <Textarea
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            placeholder={t('commentPlaceholder')}
            rows={2}
            maxLength={2000}
            data-testid="skill-feedback-comment"
          />
          <p className="moldy-ui-micro text-muted-foreground">{t('commentHint')}</p>
        </div>
      ) : null}
      {mine?.comment ? (
        <p className="mt-2 rounded-lg bg-muted/40 p-2 text-xs" data-testid="skill-feedback-mine">
          {mine.comment}
        </p>
      ) : null}
    </section>
  )
}
