import type { DeepAgentTodo } from '@/lib/chat/langgraph-runtime/deepagents-state'
import { readAgUiChildTodos, type AgUiSubagentSnapshot } from './chat-history-children'
import type { AgUiActivitySnapshot, AgUiMessageLink } from './chat-history-types'

type ChildOwner = Pick<AgUiSubagentSnapshot, 'id' | 'subagentRunId' | 'parentMessageId'> & {
  readonly namespace?: readonly string[]
}

function sameAttempt(link: AgUiMessageLink, activity: AgUiActivitySnapshot): boolean {
  return (
    !link.physicalRunId || !activity.physicalRunId || link.physicalRunId === activity.physicalRunId
  )
}

function matchesScope(link: AgUiMessageLink, child: ChildOwner): boolean {
  return (
    link.subagentRunId === child.subagentRunId &&
    (!link.parentToolCallId || link.parentToolCallId === child.id)
  )
}

function ownsActivity(
  activity: AgUiActivitySnapshot,
  child: ChildOwner,
  links: readonly AgUiMessageLink[],
): boolean {
  if (activity.parentToolCallId !== undefined && activity.parentToolCallId !== child.id)
    return false
  const entityLinks = links.filter((link) => link.messageId === activity.messageId)
  const parent =
    activity.parentMessageId ?? entityLinks.find((link) => link.parentMessageId)?.parentMessageId
  if (
    entityLinks.some(
      (link) =>
        !matchesScope(link, child) ||
        !sameAttempt(link, activity) ||
        (parent && link.parentMessageId && link.parentMessageId !== parent),
    )
  )
    return false
  if (!parent) return true

  // An activity's parent can be a message *within* the child, or the public
  // wire alias of the message containing the delegation.
  const owners = links.filter((link) => link.messageId === parent || link.uiMessageId === parent)
  if (!owners.length) return parent === child.parentMessageId || parent === child.subagentRunId
  const parentLane = child.namespace?.at(-2)
  return owners.every((link) => {
    if (!sameAttempt(link, activity)) return false
    return (
      matchesScope(link, child) ||
      (link.subagentRunId === parentLane &&
        (link.messageId === child.parentMessageId || link.uiMessageId === child.parentMessageId))
    )
  })
}

/** A missing update leaves the stored plan in place; an empty update clears it. */
export function selectAgUiChildTodos(
  activities: readonly AgUiActivitySnapshot[],
  child: ChildOwner,
  links: readonly AgUiMessageLink[] = [],
): readonly DeepAgentTodo[] | undefined {
  if (!child.subagentRunId || !child.id || !child.parentMessageId) return
  for (let index = activities.length - 1; index >= 0; index--) {
    const activity = activities[index]
    if (
      activity.type !== 'ACTIVITY_SNAPSHOT' ||
      activity.activityType !== 'moldy.subagent_todos' ||
      activity.subagentRunId !== child.subagentRunId ||
      !ownsActivity(activity, child, links)
    )
      continue
    const todos = readAgUiChildTodos(activity.content.todos)
    if (todos !== undefined) return todos
  }
}
