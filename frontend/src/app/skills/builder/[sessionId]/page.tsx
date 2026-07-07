import { SkillBuilderChatClient } from './_components/skill-builder-chat-client'

export default async function SkillBuilderChatPage({
  params,
}: {
  params: Promise<{ sessionId: string }>
}) {
  const { sessionId } = await params
  return <SkillBuilderChatClient sessionId={sessionId} />
}
