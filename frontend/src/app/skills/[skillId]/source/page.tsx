import { SkillTabPageClient } from '../_components/skill-tab-page-client'

export default async function SkillSourcePage({
  params,
}: {
  params: Promise<{ skillId: string }>
}) {
  const { skillId } = await params
  return <SkillTabPageClient skillId={skillId} tab="source" />
}
