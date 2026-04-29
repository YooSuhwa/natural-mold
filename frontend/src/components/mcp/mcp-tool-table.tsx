'use client'

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Checkbox } from '@/components/ui/checkbox'
import type { McpTool } from '@/lib/types/mcp'

interface McpToolTableProps {
  tools: McpTool[]
  /** Optional toggler — local state in wizard mode. */
  selected?: Set<string>
  onToggle?: (toolName: string) => void
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
    <div className="rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            {onToggle && <TableHead className="w-8" />}
            <TableHead>Tool</TableHead>
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
              <TableCell className="font-medium">{tool.name}</TableCell>
              <TableCell className="text-xs text-muted-foreground">
                {tool.description ?? '—'}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
