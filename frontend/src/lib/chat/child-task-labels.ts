import type { ChildTask } from '@/lib/api/child-tasks'
import { resolveSubagentDisplayName } from '@/lib/stores/chat-subagent-names'

export function childTaskLabels(
  tasks: readonly ChildTask[],
  names: Record<string, string> | undefined,
): Map<string, string> {
  const labels = new Map<string, string>()
  const seen = new Set<string>()
  const repeated = new Set<string>()
  for (const task of tasks) {
    const name = resolveSubagentDisplayName(names, task.role)
    labels.set(task.id, name)
    if (seen.has(name)) repeated.add(name)
    seen.add(name)
  }
  for (const [id, name] of labels) {
    if (repeated.has(name)) labels.set(id, `${name} · ${id.slice(0, 8)}`)
  }
  return labels
}
