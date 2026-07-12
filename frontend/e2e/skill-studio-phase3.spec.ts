import { test, expect } from './fixtures'

// Phase 3 — 평가 탭 실측 지표: A/B 벤치마크, usage/비용, 버전별 통과율,
// 휴먼 피드백(스킬/케이스) 상호작용, 히스토리 배지, 실단가 estimate.

const now = '2026-07-12T00:00:00.000Z'
const SKILL_ID = 'skill-alpha'
const SET_ID = '00000000-0000-4000-8000-0000000000e1'
const RUN_ID = '00000000-0000-4000-8000-0000000000a1'

const skill = {
  id: SKILL_ID,
  name: 'Alpha Notes',
  slug: SKILL_ID,
  description: 'Alpha Notes 설명',
  kind: 'text',
  version: '1.1.0',
  storage_path: null,
  content_hash: 'hash-v2',
  size_bytes: 256,
  used_by_count: 1,
  package_metadata: null,
  current_revision_id: null,
  credential_requirements: null,
  execution_profile: null,
  health: null,
  latest_evaluation_summary: null,
  last_modified_at: now,
  created_at: now,
  updated_at: now,
  origin_summary: null,
  publication_summary: null,
  installation: null,
}

const completedRun = {
  id: RUN_ID,
  skill_id: SKILL_ID,
  evaluation_set_id: SET_ID,
  status: 'completed',
  skill_version: '1.1.0',
  skill_content_hash: 'hash-v2',
  runner_model: 'scripted-eval-model',
  summary: {
    pass_rate: 0.95,
    case_count: 2,
    passed_count: 2,
    failed_count: 0,
    average_duration_ms: 900,
    token_delta: -120,
  },
  benchmark: {
    measured: true,
    baseline_skipped: false,
    with_skill_pass_rate: 0.95,
    without_skill_pass_rate: 0.3,
    pass_rate_delta: 0.65,
    token_delta: -120,
    duration_delta_ms: 850,
    quality_delta: 0.65,
  },
  usage: {
    measured: true,
    model_calls: 6,
    tokens_in: 3520,
    tokens_out: 720,
    cost_usd: 0.0184,
  },
  case_results: [
    {
      case_index: 0,
      name: '회의록 액션아이템',
      status: 'passed',
      score: 0.95,
      baseline_status: 'failed',
      baseline_score: 0.3,
      grader_feedback: 'with-arm이 표 형식을 정확히 지켰습니다.',
      evidence: '담당자/마감일 표 일치',
      with_answer_preview: '| 담당자 | 마감일 |',
      without_answer_preview: '일반 요약만 제공',
    },
    {
      case_index: 1,
      name: '요약 케이스',
      status: 'passed',
      score: 0.9,
      baseline_status: 'failed',
      baseline_score: 0.2,
      grader_feedback: '스킬 지침 준수.',
      evidence: '핵심 항목 포함',
    },
  ],
  error_message: null,
  cancellation_requested_at: null,
  cancellation_reason: null,
  started_at: now,
  completed_at: now,
  created_at: now,
  updated_at: now,
}

const evaluationSet = {
  id: SET_ID,
  skill_id: SKILL_ID,
  name: '품질 평가',
  description: '핵심 응답 품질',
  source_kind: 'builder',
  evals: [{ input: '질문 1' }, { input: '질문 2' }],
  expectations_schema_version: 1,
  latest_run: completedRun,
  created_at: now,
  updated_at: now,
}

const versionStats = [
  {
    skill_version: '1.0.0',
    content_hash: 'hash-v1',
    run_count: 2,
    latest_pass_rate: 0.5,
    avg_pass_rate: 0.45,
    latest_pass_rate_delta: 0.2,
    latest_measured: true,
    first_run_at: now,
    last_run_at: now,
  },
  {
    skill_version: '1.1.0',
    content_hash: 'hash-v2',
    run_count: 1,
    latest_pass_rate: 0.95,
    avg_pass_rate: 0.95,
    latest_pass_rate_delta: 0.65,
    latest_measured: true,
    first_run_at: now,
    last_run_at: now,
  },
]

const usageSummary = {
  skill_id: SKILL_ID,
  days: 30,
  tokens_in: 3520,
  tokens_out: 720,
  cost_usd: 0.0184,
  priced_event_count: 1,
  unpriced_token_event_count: 0,
  evaluation_run_count: 3,
  chat_execution_count: 5,
  daily: [],
}

const revisions = [
  {
    id: 'rev-2',
    revision_number: 2,
    operation: 'update',
    skill_version: '1.1.0',
    content_hash: 'hash-v2',
    file_count: 2,
    size_bytes: 300,
    changelog_summary: '표 형식 강화',
    created_at: now,
  },
  {
    id: 'rev-1',
    revision_number: 1,
    operation: 'create',
    skill_version: '1.0.0',
    content_hash: 'hash-v1',
    file_count: 1,
    size_bytes: 200,
    changelog_summary: '최초 생성',
    created_at: now,
  },
]

type FeedbackState = {
  upCount: number
  downCount: number
  mine: { rating: string; comment: string | null; updated_at: string } | null
}

type CaseFeedbackRow = {
  run_id: string
  case_index: number
  verdict: string
  comment: string | null
  updated_at: string
}

async function mockPhase3Apis(page: import('@playwright/test').Page) {
  const state = {
    feedback: { upCount: 0, downCount: 0, mine: null } as FeedbackState,
    caseFeedback: [] as CaseFeedbackRow[],
    estimateRequests: 0,
  }

  await page.route('**/api/skill-builder**', (route) => route.fulfill({ json: [] }))
  await page.route('**/api/skills**', async (route) => {
    const url = new URL(route.request().url())
    const method = route.request().method()
    const pathName = url.pathname

    // --- 스킬 피드백 (GET/PUT) ---
    if (pathName === `/api/skills/${SKILL_ID}/feedback`) {
      if (method === 'PUT') {
        const body = route.request().postDataJSON() as { rating: string; comment?: string | null }
        state.feedback = {
          upCount: body.rating === 'up' ? 1 : 0,
          downCount: body.rating === 'down' ? 1 : 0,
          mine: { rating: body.rating, comment: body.comment ?? null, updated_at: now },
        }
      }
      if (method === 'DELETE') {
        state.feedback = { upCount: 0, downCount: 0, mine: null }
        return route.fulfill({ status: 204 })
      }
      return route.fulfill({
        json: {
          skill_id: SKILL_ID,
          up_count: state.feedback.upCount,
          down_count: state.feedback.downCount,
          mine: state.feedback.mine,
        },
      })
    }

    // --- 케이스 피드백 ---
    if (pathName.endsWith(`/runs/${RUN_ID}/case-feedback`)) {
      if (method === 'PUT') {
        const body = route.request().postDataJSON() as {
          case_index: number
          verdict: string
          comment?: string | null
        }
        state.caseFeedback = [
          ...state.caseFeedback.filter((row) => row.case_index !== body.case_index),
          {
            run_id: RUN_ID,
            case_index: body.case_index,
            verdict: body.verdict,
            comment: body.comment ?? null,
            updated_at: now,
          },
        ]
        return route.fulfill({
          json: state.caseFeedback.find((row) => row.case_index === body.case_index),
        })
      }
      return route.fulfill({ json: state.caseFeedback })
    }

    // --- 평가/집계/usage ---
    if (pathName === `/api/skills/${SKILL_ID}/evaluations/version-stats`) {
      return route.fulfill({ json: versionStats })
    }
    if (pathName === `/api/skills/${SKILL_ID}/usage`) {
      return route.fulfill({ json: usageSummary })
    }
    if (pathName === `/api/skills/${SKILL_ID}/evaluations/${SET_ID}/estimate`) {
      state.estimateRequests += 1
      const body = route.request().postDataJSON() as { baseline_comparison?: boolean } | null
      const baseline = body?.baseline_comparison ?? true
      return route.fulfill({
        json: {
          case_count: 2,
          // 3 arms/case with baseline, 2 without (Phase 3 §4).
          model_call_count: baseline ? 6 : 4,
          estimated_seconds: baseline ? 24 : 16,
          timeout_seconds: 180,
          estimated_tokens_in: 5200,
          estimated_tokens_out: 2400,
          estimated_cost_usd: baseline ? 0.0231 : 0.0154,
          pricing_available: true,
          runner_model: 'scripted-eval-model',
          uses_baseline_comparison: baseline,
        },
      })
    }
    if (pathName === `/api/skills/${SKILL_ID}/evaluations/${SET_ID}/runs`) {
      if (method === 'POST') {
        return route.fulfill({ status: 201, json: completedRun })
      }
      return route.fulfill({ json: [completedRun] })
    }
    if (pathName === `/api/skills/${SKILL_ID}/evaluations`) {
      return route.fulfill({ json: [evaluationSet] })
    }
    if (pathName === `/api/skills/${SKILL_ID}/revisions`) {
      return route.fulfill({ json: revisions })
    }
    if (pathName === `/api/skills/${SKILL_ID}/revisions/rev-2`) {
      return route.fulfill({ json: { revision: revisions[0], files: [], evaluation: null } })
    }
    if (pathName === `/api/skills/${SKILL_ID}/credential-requirements`) {
      return route.fulfill({ json: [] })
    }
    if (pathName === `/api/skills/${SKILL_ID}/credential-bindings`) {
      return route.fulfill({ json: [] })
    }
    if (pathName === `/api/skills/${SKILL_ID}`) {
      return route.fulfill({ json: skill })
    }
    if (pathName === '/api/skills') {
      return route.fulfill({ json: [skill] })
    }
    return route.fulfill({ status: 404, json: { detail: pathName } })
  })
  return state
}

test.describe('Skill studio phase 3 — measured evaluation surfaces', () => {
  test('evaluation tab renders measured A/B benchmark, usage and version stats', async ({
    page,
  }) => {
    await mockPhase3Apis(page)
    await page.goto(`/skills/${SKILL_ID}/evaluation`)

    // usage 카드 — 실측 토큰/비용/실행 카운트.
    const usageCard = page.getByTestId('skill-usage-summary-card')
    await expect(usageCard).toBeVisible()
    await expect(usageCard).toContainText('4,240')
    await expect(usageCard).toContainText('$0.0184')

    // 버전별 통과율 — 두 버전 바.
    const versionPanel = page.getByTestId('skill-version-pass-rate-panel')
    await expect(versionPanel).toContainText('1.0.0')
    await expect(versionPanel).toContainText('50%')
    await expect(versionPanel).toContainText('1.1.0')
    await expect(versionPanel).toContainText('95%')

    // A/B 벤치마크 — 실측 배지 + with/without 바.
    const benchmark = page.getByTestId('skill-benchmark-panel')
    await expect(benchmark.getByTestId('benchmark-measured')).toBeVisible()
    await expect(benchmark).toContainText('스킬 사용')
    await expect(benchmark).toContainText('스킬 없이')
    await expect(benchmark).toContainText('30%')

    // 런 실측 usage 라인.
    await expect(page.getByTestId('run-usage-line')).toContainText('모델 콜 6회')
  })

  test('skill feedback rating roundtrips through the API', async ({ page }) => {
    await mockPhase3Apis(page)
    await page.goto(`/skills/${SKILL_ID}/evaluation`)

    const upButton = page.getByTestId('skill-feedback-up')
    await expect(upButton).toContainText('0')
    await upButton.click()
    await expect(upButton).toContainText('1')
  })

  test('case feedback verdict persists per case', async ({ page }) => {
    const state = await mockPhase3Apis(page)
    await page.goto(`/skills/${SKILL_ID}/evaluation`)

    await page.getByTestId('case-feedback-agree-0').click()
    await expect
      .poll(() => state.caseFeedback.map((row) => `${row.case_index}:${row.verdict}`))
      .toEqual(['0:agree'])

    await page.getByTestId('case-feedback-disagree-1').click()
    await expect
      .poll(() => state.caseFeedback.map((row) => `${row.case_index}:${row.verdict}`).sort())
      .toEqual(['0:agree', '1:disagree'])
  })

  test('history tab shows pass-rate badges from version stats', async ({ page }) => {
    await mockPhase3Apis(page)
    await page.goto(`/skills/${SKILL_ID}/versions`)

    const badges = page.getByTestId('revision-pass-rate')
    await expect(badges).toHaveCount(2)
    await expect(badges.first()).toContainText('95%')
    await expect(badges.nth(1)).toContainText('50%')
  })

  test('estimate dialog surfaces priced cost and runner model', async ({ page }) => {
    await mockPhase3Apis(page)
    await page.goto(`/skills/${SKILL_ID}/evaluation`)

    await page.getByRole('button', { name: /다시 실행/ }).first().click()
    await expect(page.getByTestId('estimate-cost')).toContainText('$0.0231')
    await expect(page.getByText('scripted-eval-model')).toBeVisible()
  })

  test('baseline toggle re-estimates with a cheaper 2-arm run', async ({ page }) => {
    await mockPhase3Apis(page)
    await page.goto(`/skills/${SKILL_ID}/evaluation`)

    await page.getByRole('button', { name: /다시 실행/ }).first().click()
    const toggle = page.getByTestId('estimate-baseline-toggle')
    await expect(toggle).toBeChecked()
    await expect(page.getByTestId('estimate-cost')).toContainText('$0.0231')

    await toggle.click()
    await expect(toggle).not.toBeChecked()
    // The refetched estimate reflects the skipped without-arm.
    await expect(page.getByTestId('estimate-cost')).toContainText('$0.0154')
  })
})
