import { redirect } from 'next/navigation'

import { SkillsPageClient } from './_components/skills-page-client'
import { legacyDetailTabToStudioTab } from './_lib/skill-studio-tabs'

/**
 * 목록 탭. 레거시 `?detailId=&tab=` 딥링크(구 상세 다이얼로그)는 스튜디오
 * 라우트로 서버 redirect한다 — 외부 도메인에서 새 생성처가 재발해도 여기가
 * 영구 안전망 (Phase 2 스펙 AD-1).
 */
export default async function SkillsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>
}) {
  const params = await searchParams
  const detailId = typeof params.detailId === 'string' ? params.detailId : null
  // encodeURIComponent는 dot-segment(`.`/`..`)를 안 이스케이프해서
  // `?detailId=..`가 `/skills/../source` → 브라우저 정규화로 /skills 밖으로
  // 탈출한다 (R5). 단일 세그먼트 문자 집합 + dot-segment 명시 거부만 통과
  // (UUID 강제는 비 UUID 픽스처를 쓰는 mock E2E 계약을 깬다). 비정상 값은
  // 목록으로 수렴.
  if (detailId && SAFE_DETAIL_ID.test(detailId)) {
    const tab = typeof params.tab === 'string' ? params.tab : null
    redirect(`/skills/${encodeURIComponent(detailId)}/${legacyDetailTabToStudioTab(tab)}`)
  }
  return <SkillsPageClient />
}

const SAFE_DETAIL_ID = /^(?!\.{1,2}$)[A-Za-z0-9._-]+$/
