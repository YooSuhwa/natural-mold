'use client'

import { useState } from 'react'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Checkbox } from '@/components/ui/checkbox'

/**
 * Minimal shape both ``McpTool`` (persisted) and ``McpProbeTool`` (preview)
 * satisfy — lets the same table render results from either source.
 */
interface McpToolLike {
  id?: string
  name: string
  description: string | null
  enabled?: boolean
}

interface McpToolTableProps {
  tools: McpToolLike[]
  /** Optional toggler — local state in wizard mode. */
  selected?: Set<string>
  onToggle?: (toolName: string) => void
}

function ExpandableDescription({ text }: { text: string | null | undefined }) {
  const [expanded, setExpanded] = useState(false)
  if (!text) return <span>—</span>
  return (
    <button
      type="button"
      onClick={() => setExpanded((v) => !v)}
      title={expanded ? 'Click to collapse' : 'Click to expand'}
      className={`block w-full whitespace-normal break-words text-left transition-colors hover:text-foreground ${
        expanded ? '' : 'line-clamp-3'
      }`}
    >
      {text}
    </button>
  )
}

export function McpToolTable({ tools, selected, onToggle }: McpToolTableProps) {
  if (tools.length === 0) {
    return (
      <p className="rounded border border-dashed p-3 text-center text-xs text-muted-foreground">
        No tools discovered yet.
      </p>
    )
  }
  return (
    <div className="w-full overflow-hidden rounded-lg border">
      <Table className="w-full table-fixed">
        <TableHeader>
          <TableRow>
            {onToggle && <TableHead className="w-8" />}
            <TableHead className="w-1/3">Tool</TableHead>
            <TableHead>Description</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {tools.map((tool) => (
            <TableRow key={tool.id ?? tool.name}>
              {onToggle && (
                <TableCell>
                  <Checkbox
                    checked={selected?.has(tool.name) ?? tool.enabled}
                    onCheckedChange={() => onToggle(tool.name)}
                  />
                </TableCell>
              )}
              <TableCell className="break-words font-medium">
                {tool.name}
              </TableCell>
              <TableCell className="text-xs text-muted-foreground">
                <ExpandableDescription text={tool.description} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
