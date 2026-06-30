/**
 * Generative UI data-event contract (chat-generative-ui-dev-plan §5.2).
 * 1:1 with the backend ``UIDataEvent`` (``app/schemas/ui_data.py``).
 */

/** Wire payload of a ``moldy.ui_data`` custom SSE event. */
export interface UIDataEventPayload {
  schema_version?: number
  type: string
  message_id?: string | null
  run_id?: string | null
  tool_call_id?: string | null
  props: Record<string, unknown>
}

/**
 * Normalized item attached to a message (consumed by the converter to inject an
 * assistant-ui data part). The registry resolves ``type`` → component and Zod
 * validates ``props``.
 */
export interface UIDataItem {
  type: string
  props: Record<string, unknown>
  tool_call_id?: string | null
}
