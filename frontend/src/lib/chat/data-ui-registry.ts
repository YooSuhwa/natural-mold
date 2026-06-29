import type { ComponentType } from 'react'
import { z } from 'zod'
import { DemoNoteCard } from '@/components/chat/data-ui/demo-note-card'
import { DataTableCard } from '@/components/chat/data-ui/data-table-card'
import { ChartCard } from '@/components/chat/data-ui/chart-card'
import { StatsCard } from '@/components/chat/data-ui/stats-card'

/**
 * Generative UI allowlist registry (chat-generative-ui-dev-plan §5.2). Maps a
 * payload ``type`` to a Zod props schema + React component. Unknown types and
 * props that fail validation resolve to ``null`` (fail-safe) — the renderer then
 * skips them, so a malformed/untrusted payload can never render arbitrary
 * content (R2 security).
 */

/**
 * assistant-ui data-part ``name`` carrying generative UI payloads. The producer
 * (converter) injects ``{type:'data', name, data:{type, props}}``; the
 * ``makeAssistantDataUI({name})`` renderer dispatches via {@link resolveDataUI}.
 */
export const MOLDY_UI_DATA_PART_NAME = 'moldy_ui'

interface DataUIEntry<P> {
  readonly props: z.ZodType<P>
  readonly Component: ComponentType<P>
}

// Keeps each entry's props schema and component prop types aligned.
function defineDataUI<P>(props: z.ZodType<P>, Component: ComponentType<P>): DataUIEntry<P> {
  return { props, Component }
}

const demoNoteProps = z.object({ text: z.string() })

const dataTableProps = z.object({
  columns: z.array(z.object({ key: z.string(), header: z.string() })),
  rows: z.array(z.record(z.string(), z.unknown())),
  title: z.string().optional(),
  searchable: z.boolean().optional(),
})

const chartProps = z.object({
  chartType: z.enum(['line', 'bar']),
  series: z.array(z.object({ label: z.string(), value: z.number() })),
  title: z.string().optional(),
  xLabel: z.string().optional(),
  yLabel: z.string().optional(),
})

const statsProps = z.object({
  items: z.array(
    z.object({
      label: z.string(),
      value: z.union([z.string(), z.number()]),
      delta: z.number().optional(),
      unit: z.string().optional(),
    }),
  ),
})

// Each entry is internally type-aligned via defineDataUI. Phase 2 extends:
// terminal.
export const DATA_UI_REGISTRY = {
  demo_note: defineDataUI(demoNoteProps, DemoNoteCard),
  data_table: defineDataUI(dataTableProps, DataTableCard),
  chart: defineDataUI(chartProps, ChartCard),
  stats: defineDataUI(statsProps, StatsCard),
}

export interface ResolvedDataUI {
  readonly Component: ComponentType<Record<string, unknown>>
  readonly props: Record<string, unknown>
}

/**
 * Resolve a ui_data ``type`` + raw props to a renderable component, or ``null``
 * when the type is unknown or the props fail Zod validation (fail-safe).
 */
export function resolveDataUI(type: string, rawProps: unknown): ResolvedDataUI | null {
  const entry = (DATA_UI_REGISTRY as Record<string, DataUIEntry<unknown>>)[type]
  if (!entry) return null
  const parsed = entry.props.safeParse(rawProps)
  if (!parsed.success) return null
  return {
    Component: entry.Component as ComponentType<Record<string, unknown>>,
    props: parsed.data as Record<string, unknown>,
  }
}
