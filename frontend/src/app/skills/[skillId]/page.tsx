import { redirect } from 'next/navigation'

/** 스킬 스코프 기본 탭 — 레거시 content 탭의 대응인 소스로 보낸다 (스펙 AD-1). */
export default async function SkillIndexPage({ params }: { params: Promise<{ skillId: string }> }) {
  const { skillId } = await params
  redirect(`/skills/${skillId}/source`)
}
