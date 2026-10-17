import type { Message } from '@/lib/types'

/**
 * After a reload the legacy runtime has no decision coordinator: the pending card comes
 * from the messages API (backend hydration only adds still-pending cards). A
 * single-action card of that interrupt can then be answered directly (G-05); anything
 * else stays refused as "no longer active".
 */
export function hasHydratedSingleActionInterrupt(
  messages: readonly Message[],
  interruptId: string,
): boolean {
  return messages.some((message) =>
    (message.tool_calls ?? []).some(
      (call) => call.args.hitl_interrupt_id === interruptId && call.args.hitl_total_actions === 1,
    ),
  )
}
