import { mkdir } from 'node:fs/promises'
import path from 'node:path'

import { test, expect, isRecord } from './fixtures'

const now = '2026-06-15T00:00:00.000Z'
const requestText = '회의록에서 액션 아이템을 표로 정리하는 스킬을 만들어줘'
const sessionId = 'session-create-skill'

const createdSkill = {
  id: 'skill-builder-created',
  name: '회의록 액션 아이템',
  slug: 'meeting-actions',
  description: '회의록에서 담당자, 할 일, 마감일을 표로 정리합니다.',
  kind: 'package',
  version: '1.0.0',
  storage_path: null,
  content_hash: 'hash-created',
  size_bytes: 2048,
  used_by_count: 0,
  package_metadata: null,
  current_revision_id: 'revision-created',
  health: { state: 'ready', reason: 'Latest evaluation passed.' },
  latest_evaluation_summary: {
    status: 'passed',
    pass_rate: 1,
    evaluation_set_id: 'eval-set-created',
    run_id: 'eval-run-created',
  },
  last_modified_at: now,
  created_at: now,
  updated_at: now,
  origin_summary: null,
  publication_summary: null,
  installation: null,
}

const draftPackage = {
  name: createdSkill.name,
  slug: createdSkill.slug,
  description: createdSkill.description,
  files: [
    {
      path: 'SKILL.md',
      content:
        '---\nname: meeting-actions\ndescription: 회의록에서 액션 아이템을 정리합니다.\n---\n\n회의록을 표로 정리합니다.',
      media_type: 'text/markdown',
      role: 'skill',
    },
    {
      path: 'agents/openai.yaml',
      content: 'name: meeting-actions\nversion: 1\n',
      media_type: 'application/yaml',
      role: 'metadata',
    },
  ],
  credential_requirements: [],
  execution_profile: {},
  validation_issues: [],
  compatibility_result: {
    targets: {
      openai_codex: { status: 'pass', issues: [] },
      claude_code: { status: 'pass', issues: [] },
      vercel_agent_skills: { status: 'pass', issues: [] },
    },
    error_count: 0,
    warning_count: 0,
    info_count: 0,
  },
  changelog_draft: {
    summary: '회의록 액션 아이템 스킬 초안 생성',
    items: [{ title: 'OpenAI metadata added', path: 'agents/openai.yaml' }],
  },
  evals: null,
  benchmark: { pass_rate: 1, mean_score: 1, delta: 1 },
}

const reviewSession = {
  id: sessionId,
  user_id: 'user-1',
  user_request: requestText,
  mode: 'create',
  status: 'review',
  current_phase: 3,
  source_skill_id: null,
  base_skill_version: null,
  base_content_hash: null,
  base_snapshot: null,
  draft_package: draftPackage,
  validation_result: {
    error_count: 0,
    warning_count: 0,
    info_count: 1,
    issues: [{ severity: 'info', message: '포터블 스킬 구조가 유효합니다.' }],
  },
  compatibility_result: draftPackage.compatibility_result,
  changelog_draft: draftPackage.changelog_draft,
  eval_result: { summary: { pass_rate: 1 } },
  trigger_eval_result: null,
  finalized_skill_id: null,
  error_message: null,
  created_at: now,
  updated_at: now,
}

function sse(event: string, data: Record<string, unknown>): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
}

test.describe('Skill Builder create flow', () => {
  test('opens from the skills CTA and stays on the builder workflow surface', async ({ page }) => {
    const forbiddenRuntimeCalls: string[] = []
    let confirmed = false
    let startCalls = 0
    let streamCalls = 0

    await page.route('**/api/conversations**', (route) => {
      forbiddenRuntimeCalls.push(route.request().url())
      return route.fulfill({ status: 418, json: { detail: 'unexpected conversation runtime' } })
    })
    await page.route('**/threads/**', (route) => {
      forbiddenRuntimeCalls.push(route.request().url())
      return route.fulfill({ status: 418, json: { detail: 'unexpected thread runtime' } })
    })

    await page.route('**/api/skills**', (route) => {
      const url = new URL(route.request().url())
      const method = route.request().method()
      const pathName = url.pathname

      if (method === 'GET' && pathName === '/api/skills') {
        return route.fulfill({ json: confirmed ? [createdSkill] : [] })
      }
      if (method === 'GET' && pathName === `/api/skills/${createdSkill.id}`) {
        return route.fulfill({ json: createdSkill })
      }
      if (method === 'GET' && pathName === `/api/skills/${createdSkill.id}/files`) {
        return route.fulfill({
          json: [
            { path: 'SKILL.md', is_dir: false, size: 104, modified_at: now },
            { path: 'agents/openai.yaml', is_dir: false, size: 32, modified_at: now },
          ],
        })
      }
      if (method === 'GET' && pathName === `/api/skills/${createdSkill.id}/files/SKILL.md`) {
        return route.fulfill({
          contentType: 'text/markdown; charset=utf-8',
          body: draftPackage.files[0]?.content ?? '',
        })
      }
      if (
        method === 'GET' &&
        pathName === `/api/skills/${createdSkill.id}/files/agents/openai.yaml`
      ) {
        return route.fulfill({
          contentType: 'application/yaml; charset=utf-8',
          body: draftPackage.files[1]?.content ?? '',
        })
      }

      return route.fulfill({ status: 404, json: { detail: pathName } })
    })

    await page.route('**/api/skill-builder**', (route) => {
      const url = new URL(route.request().url())
      const method = route.request().method()
      const pathName = url.pathname

      if (method === 'POST' && pathName === '/api/skill-builder') {
        startCalls += 1
        const body: unknown = route.request().postDataJSON()
        expect(isRecord(body)).toBeTruthy()
        if (isRecord(body)) {
          expect(body.mode).toBe('create')
          expect(body.user_request).toBe(requestText)
        }
        return route.fulfill({ json: { ...reviewSession, status: 'drafting' } })
      }
      if (method === 'POST' && pathName === `/api/skill-builder/${sessionId}/messages`) {
        streamCalls += 1
        const body: unknown = route.request().postDataJSON()
        expect(isRecord(body)).toBeTruthy()
        if (isRecord(body)) {
          expect(body.content).toBe(requestText)
        }
        return route.fulfill({
          contentType: 'text/event-stream; charset=utf-8',
          body: [
            sse('builder_status', { session_id: sessionId, status: 'drafting' }),
            sse('draft_package', { session_id: sessionId }),
            sse('validation_result', { session_id: sessionId }),
            sse('compatibility_result', { session_id: sessionId }),
            sse('message_end', { session_id: sessionId, status: 'review' }),
          ].join(''),
        })
      }
      if (method === 'GET' && pathName === `/api/skill-builder/${sessionId}`) {
        return route.fulfill({ json: reviewSession })
      }
      if (method === 'POST' && pathName === `/api/skill-builder/${sessionId}/confirm`) {
        confirmed = true
        return route.fulfill({ json: createdSkill })
      }

      return route.fulfill({ status: 404, json: { detail: pathName } })
    })

    await page.goto('/skills')
    await page.getByRole('button', { name: '대화로 첫 스킬 만들기' }).click()
    const createDialog = page.getByRole('dialog', { name: '새 스킬' })
    await createDialog.getByLabel('요청').fill(requestText)
    await createDialog.getByRole('button', { name: '대화 시작' }).click()

    await expect(createDialog).toBeHidden()
    const builderDialog = page.getByRole('dialog', { name: '대화로 스킬 만들기' })
    await expect(builderDialog).toBeVisible()
    await expect(page).toHaveURL(/\/skills$/)
    expect(forbiddenRuntimeCalls).toEqual([])

    await builderDialog.getByRole('button', { name: '대화 시작' }).click()
    await expect(builderDialog.getByText('SKILL.md', { exact: true }).first()).toBeVisible()
    await expect(
      builderDialog.getByText('agents/openai.yaml', { exact: true }).first(),
    ).toBeVisible()
    await expect(builderDialog.getByText('공용 호환성')).toBeVisible()
    await expect(builderDialog.getByText('OpenAI/Codex')).toBeVisible()
    await expect(builderDialog.getByText('Claude Code')).toBeVisible()
    await expect(builderDialog.getByText('Vercel Agent Skills')).toBeVisible()

    await builderDialog.getByRole('button', { name: '스킬로 저장' }).click()
    await expect(builderDialog).toBeHidden()
    await expect(page.getByText(createdSkill.name).first()).toBeVisible({ timeout: 15_000 })
    await expect(page.getByText('평가 100%').first()).toBeVisible()
    await expect(page.getByRole('dialog', { name: /회의록 액션 아이템/ })).toBeVisible()

    expect(startCalls).toBe(1)
    expect(streamCalls).toBe(1)
    expect(forbiddenRuntimeCalls).toEqual([])

    const captureDir = path.resolve(process.cwd(), '../output/e2e-captures/20260615-skill-builder')
    await mkdir(captureDir, { recursive: true })
    await page.screenshot({
      path: path.join(captureDir, 'builder-create-flow.png'),
      fullPage: false,
    })
  })
})
