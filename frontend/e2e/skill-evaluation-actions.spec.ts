import { mkdir } from 'node:fs/promises'
import path from 'node:path'

import { test, expect } from './fixtures'

const now = '2026-06-01T00:00:00.000Z'

const skill = {
  id: 'skill-visual',
  name: 'Korea Weather',
  slug: 'korea-weather',
  description: '한국 날씨 응답을 안정적으로 정리합니다.',
  kind: 'package',
  version: '0.1.0',
  storage_path: null,
  content_hash: 'hash-current',
  size_bytes: 2048,
  used_by_count: 1,
  package_metadata: null,
  health: {
    state: 'evaluation_running',
    label: '평가 중',
    reason: 'Latest evaluation is still running.',
    severity: 'info',
  },
  latest_evaluation_summary: {
    status: 'running',
    latest_run_id: 'run-active',
    evaluation_set_id: 'set-active',
    pass_rate: 0.5,
    skill_content_hash: 'hash-current',
    created_at: now,
    completed_at: null,
  },
  last_modified_at: now,
  created_at: now,
  updated_at: now,
  origin_summary: null,
  publication_summary: null,
  installation: null,
}

const activeEvaluationSet = {
  id: 'set-active',
  skill_id: 'skill-visual',
  name: '핵심 평가',
  description: '현재 실행 중인 응답 품질 평가입니다.',
  source_kind: 'generated',
  evals: [{ input: '서울 날씨 요약', expected: '간결한 한국어 요약' }],
  expectations_schema_version: 1,
  latest_run: {
    id: 'run-active',
    skill_id: 'skill-visual',
    evaluation_set_id: 'set-active',
    status: 'running',
    summary: { pass_rate: 0.5 },
    benchmark: null,
    case_results: null,
    error_message: null,
    cancellation_requested_at: null,
    cancellation_reason: null,
    skill_version: '0.1.0',
    skill_content_hash: 'hash-current',
    runner_model: 'gpt-5-mini',
    started_at: now,
    completed_at: null,
    created_at: now,
    updated_at: now,
  },
}

const completedEvaluationSet = {
  id: 'set-complete',
  skill_id: 'skill-visual',
  name: '회귀 평가',
  description: '완료된 평가를 다시 실행할 수 있어야 합니다.',
  source_kind: 'generated',
  evals: [{ input: '부산 날씨 요약', expected: '간결한 한국어 요약' }],
  expectations_schema_version: 1,
  latest_run: {
    id: 'run-complete',
    skill_id: 'skill-visual',
    evaluation_set_id: 'set-complete',
    status: 'completed',
    summary: { pass_rate: 0.92 },
    benchmark: null,
    case_results: null,
    error_message: null,
    cancellation_requested_at: null,
    cancellation_reason: null,
    skill_version: '0.1.0',
    skill_content_hash: 'hash-current',
    runner_model: 'gpt-5-mini',
    started_at: now,
    completed_at: '2026-06-01T00:01:00.000Z',
    created_at: now,
    updated_at: '2026-06-01T00:01:00.000Z',
  },
}

const credentialRequiredSkill = {
  ...skill,
  id: 'skill-needs-credentials',
  health: {
    state: 'needs_credentials',
    label: '자격증명 필요',
    reason: '필수 자격증명이 연결되지 않았습니다.',
    severity: 'warning',
  },
  latest_evaluation_summary: {
    status: 'failed',
    latest_run_id: 'run-missing-credentials',
    evaluation_set_id: 'set-complete',
    pass_rate: 0.2,
    skill_content_hash: 'hash-current',
    created_at: now,
    completed_at: '2026-06-01T00:01:00.000Z',
  },
  credential_requirements: [
    {
      key: 'weather_key',
      definition_key: 'weather_api',
      required: true,
      label: 'Weather API',
      description: '날씨 API 키입니다.',
      fields: ['api_key'],
      injection: 'env',
      scope: 'user',
    },
  ],
}

test.describe('Skill evaluation actions', () => {
  test('shows rerun and cancel controls in the installed skill evaluation tab', async ({
    page,
  }) => {
    let rerunRequested = false
    let cancelRequested = false

    await page.route('**/api/skills**', (route) => {
      const url = new URL(route.request().url())
      const method = route.request().method()
      const pathName = url.pathname

      if (method === 'GET' && pathName === '/api/skills') {
        return route.fulfill({ json: [skill] })
      }
      if (method === 'GET' && pathName === '/api/skills/skill-visual') {
        return route.fulfill({ json: skill })
      }
      if (method === 'GET' && pathName === '/api/skills/skill-visual/evaluations') {
        return route.fulfill({ json: [activeEvaluationSet, completedEvaluationSet] })
      }
      if (
        method === 'POST' &&
        pathName === '/api/skills/skill-visual/evaluations/set-complete/estimate'
      ) {
        return route.fulfill({
          json: {
            case_count: 1,
            model_call_count: 2,
            estimated_seconds: 8,
            timeout_seconds: 60,
            estimated_cost_usd: 0.0042,
            uses_baseline_comparison: true,
          },
        })
      }
      if (
        method === 'POST' &&
        pathName === '/api/skills/skill-visual/evaluations/set-complete/runs'
      ) {
        rerunRequested = true
        return route.fulfill({
          status: 201,
          json: {
            ...completedEvaluationSet.latest_run,
            id: 'run-rerun',
            status: 'queued',
            completed_at: null,
          },
        })
      }
      if (
        method === 'POST' &&
        pathName === '/api/skills/skill-visual/evaluations/set-active/runs/run-active/cancel'
      ) {
        cancelRequested = true
        return route.fulfill({
          json: {
            ...activeEvaluationSet.latest_run,
            status: 'cancelled',
            cancellation_requested_at: now,
            cancellation_reason: 'user_requested',
          },
        })
      }

      return route.fulfill({ status: 404, json: { detail: pathName } })
    })

    await page.goto('/skills?detailId=skill-visual&tab=evaluation')
    await expect(page.getByRole('dialog', { name: /Korea Weather/ })).toBeVisible()
    await expect(page.getByRole('button', { name: '핵심 평가 평가 취소' })).toBeVisible()
    await expect(page.getByRole('button', { name: '회귀 평가 평가 다시 실행' })).toBeVisible()

    const captureDir = path.resolve(
      process.cwd(),
      '../output/e2e-captures/20260615-skill-eval-actions',
    )
    await mkdir(captureDir, { recursive: true })
    await page.screenshot({
      path: path.join(captureDir, 'evaluation-tab-actions.png'),
      fullPage: false,
    })

    await page.getByRole('button', { name: '회귀 평가 평가 다시 실행' }).click()
    await expect(page.getByRole('alertdialog', { name: '평가 실행 확인' })).toBeVisible()
    await page.screenshot({
      path: path.join(captureDir, 'evaluation-estimate-confirmation.png'),
      fullPage: false,
    })
    await page.getByRole('button', { name: '평가 실행' }).click()
    await expect(page.getByRole('alertdialog', { name: '평가 실행 확인' })).toBeHidden()
    await expect.poll(() => rerunRequested).toBe(true)

    await page.getByRole('button', { name: '핵심 평가 평가 취소' }).click()

    await expect.poll(() => cancelRequested).toBe(true)
  })

  test('opens the credentials tab instead of rerunning when required credentials are missing', async ({
    page,
  }) => {
    let estimateRequested = false

    await page.route('**/api/skills**', (route) => {
      const url = new URL(route.request().url())
      const method = route.request().method()
      const pathName = url.pathname

      if (method === 'GET' && pathName === '/api/skills') {
        return route.fulfill({ json: [credentialRequiredSkill] })
      }
      if (method === 'GET' && pathName === '/api/skills/skill-needs-credentials') {
        return route.fulfill({ json: credentialRequiredSkill })
      }
      if (method === 'GET' && pathName === '/api/skills/skill-needs-credentials/evaluations') {
        return route.fulfill({ json: [completedEvaluationSet] })
      }
      if (
        method === 'POST' &&
        pathName === '/api/skills/skill-needs-credentials/evaluations/set-complete/estimate'
      ) {
        estimateRequested = true
        return route.fulfill({ status: 409, json: { detail: 'credentials required' } })
      }
      if (
        method === 'GET' &&
        pathName === '/api/skills/skill-needs-credentials/credential-requirements'
      ) {
        return route.fulfill({ json: credentialRequiredSkill.credential_requirements })
      }
      if (
        method === 'GET' &&
        pathName === '/api/skills/skill-needs-credentials/credential-bindings'
      ) {
        return route.fulfill({ json: [] })
      }

      return route.fulfill({ status: 404, json: { detail: pathName } })
    })

    await page.goto('/skills?detailId=skill-needs-credentials&tab=evaluation')
    await expect(page.getByRole('dialog', { name: /Korea Weather/ })).toBeVisible()
    await expect(page.getByRole('button', { name: '회귀 평가 자격증명 연결' })).toBeVisible()

    await page.getByRole('button', { name: '회귀 평가 자격증명 연결' }).click()

    await expect(page.getByRole('tab', { name: /자격증명/ })).toHaveAttribute(
      'aria-selected',
      'true',
    )
    await expect(page.getByText('필수 자격증명 1개 미연결')).toBeVisible()
    await expect(page.getByText('weather_api')).toBeVisible()
    await expect.poll(() => estimateRequested).toBe(false)

    const captureDir = path.resolve(
      process.cwd(),
      '../output/e2e-captures/20260615-skill-eval-actions',
    )
    await mkdir(captureDir, { recursive: true })
    await page.screenshot({
      path: path.join(captureDir, 'evaluation-missing-credentials.png'),
      fullPage: false,
    })
  })
})
