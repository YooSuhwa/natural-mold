/** Phase 3 스킬 단위 휴먼 피드백 (표시 전용 — pass_rate/health 미반영). */

export type SkillFeedbackRating = 'up' | 'down'

export type SkillFeedbackMine = {
  readonly rating: SkillFeedbackRating | string
  readonly comment?: string | null
  readonly updated_at: string
}

export type SkillFeedbackSummary = {
  readonly skill_id: string
  readonly up_count: number
  readonly down_count: number
  readonly mine?: SkillFeedbackMine | null
}

export type SkillFeedbackUpsert = {
  readonly rating: SkillFeedbackRating
  readonly comment?: string | null
}
