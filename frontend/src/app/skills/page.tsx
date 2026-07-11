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
  if (detailId) {
    const tab = typeof params.tab === 'string' ? params.tab : null
    redirect(`/skills/${encodeURIComponent(detailId)}/${legacyDetailTabToStudioTab(tab)}`)
  }
  return <SkillsPageClient />
}
