import { SkillTabPageClient } from '../_components/skill-tab-page-client'

export default async function SkillSourcePage({
  params,
  searchParams,
}: {
  params: Promise<{ skillId: string }>
  searchParams: Promise<Record<string, string | string[] | undefined>>
}) {
  const { skillId } = await params
  const query = await searchParams
  // `?revision=` — 버전 탭의 "이 버전 소스 보기" read-only 모드 (스펙 AD-6).
  const revision = typeof query.revision === 'string' ? query.revision : null
  return <SkillTabPageClient skillId={skillId} tab="source" revisionId={revision} />
}
