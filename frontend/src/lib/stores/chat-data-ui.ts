import type { UIDataItem } from '@/lib/types/ui-data'

/**
 * ui_data items keyed by attach target (message_id, else run_id) — the same
 * keying rule as artifacts (chat-generative-ui-dev-plan §5.2). Held in the
 * ingestion hook's local state; event-level dedup happens in the hook before
 * upsert, so this reducer only appends.
 */
export type DataUIByMessageId = Record<string, UIDataItem[]>

export function upsertMessageDataUI(
  current: DataUIByMessageId,
  key: string,
  item: UIDataItem,
): DataUIByMessageId {
  const existing = current[key] ?? []
  return { ...current, [key]: [...existing, item] }
}
