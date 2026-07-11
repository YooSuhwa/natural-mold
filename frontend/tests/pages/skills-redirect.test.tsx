import { describe, expect, it, vi } from 'vitest'

// 레거시 `?detailId=&tab=` 서버 redirect의 안전망 계약 (R5) —
// encodeURIComponent는 dot-segment를 이스케이프하지 않아 `..`가
// `/skills/../source` → 브라우저 정규화로 /skills 밖으로 탈출했다.
// 안전한 단일 세그먼트(dot-segment 제외)만 redirect, 그 외는 목록 렌더.

vi.mock('next/navigation', () => ({
  redirect: vi.fn((url: string) => {
    throw new Error(`REDIRECT:${url}`)
  }),
}))

vi.mock('@/app/skills/_components/skills-page-client', () => ({
  SkillsPageClient: () => null,
}))

import SkillsPage from '@/app/skills/page'

const VALID_ID = '11111111-2222-4333-8444-555555555555'

async function run(params: Record<string, string>) {
  return SkillsPage({ searchParams: Promise.resolve(params) })
}

describe('SkillsPage legacy redirect', () => {
  it('정상 detailId는 스튜디오 라우트로 redirect한다 (tab 매핑 포함)', async () => {
    await expect(run({ detailId: VALID_ID, tab: 'history' })).rejects.toThrow(
      `REDIRECT:/skills/${VALID_ID}/versions`,
    )
    await expect(run({ detailId: VALID_ID })).rejects.toThrow(
      `REDIRECT:/skills/${VALID_ID}/source`,
    )
    // mock E2E 픽스처는 비 UUID id를 쓴다 — UUID 강제로 좁히면 이 계약이 깨진다.
    await expect(run({ detailId: 'skill-history', tab: 'history' })).rejects.toThrow(
      'REDIRECT:/skills/skill-history/versions',
    )
  })

  it('dot-segment/경로 탈출 detailId는 redirect하지 않고 목록으로 수렴한다', async () => {
    // `..` → /skills/../source → /source 탈출 클래스 (R5)
    await expect(run({ detailId: '..' })).resolves.toBeTruthy()
    await expect(run({ detailId: '.' })).resolves.toBeTruthy()
    await expect(run({ detailId: 'a/b' })).resolves.toBeTruthy()
    await expect(run({ detailId: 'a b' })).resolves.toBeTruthy()
  })
})
