import { SkillTabPageClient } from '../_components/skill-tab-page-client'

export default async function SkillVersionsPage({
  params,
}: {
  params: Promise<{ skillId: string }>
}) {
  const { skillId } = await params
  return <SkillTabPageClient skillId={skillId} tab="versions" />
}
