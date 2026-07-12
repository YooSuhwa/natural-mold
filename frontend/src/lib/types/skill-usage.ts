/** Phase 3 스킬 축 usage 요약 — 실측 귀속만 (평가 런 토큰/비용 + 채팅 실행 횟수). */

export type SkillUsageDailyPoint = {
  readonly date: string
  readonly tokens_in: number
  readonly tokens_out: number
  readonly cost_usd: number
  readonly execution_count: number
}

export type SkillUsageSummary = {
  readonly skill_id: string
  readonly days: number
  readonly tokens_in: number
  readonly tokens_out: number
  /** 단가가 알려진 이벤트의 비용 합 — 단가 없는 이벤트는 기여하지 않는다. */
  readonly cost_usd: number
  readonly priced_event_count: number
  /** 토큰은 썼지만 단가가 없어 비용을 모르는 이벤트 수 (모름 ≠ 무료). */
  readonly unpriced_token_event_count: number
  readonly evaluation_run_count: number
  readonly chat_execution_count: number
  readonly daily: readonly SkillUsageDailyPoint[]
}
