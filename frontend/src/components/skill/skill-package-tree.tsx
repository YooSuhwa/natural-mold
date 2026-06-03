'use client'

import { useMemo } from 'react'
import { ChevronRight, FileText, Folder } from 'lucide-react'
import type { SkillFileEntry } from '@/lib/types/skill'

interface SkillPackageTreeProps {
  files: SkillFileEntry[]
  onSelect?: (path: string) => void
  selectedPath?: string | null
}

interface TreeNode {
  name: string
  path: string
  isDir: boolean
  size: number
  children: TreeNode[]
}

function buildTree(files: SkillFileEntry[]): TreeNode {
  const root: TreeNode = { name: '', path: '', isDir: true, size: 0, children: [] }
  for (const file of files) {
    const parts = file.path.split('/')
    let cursor = root
    parts.forEach((part, idx) => {
      const isLast = idx === parts.length - 1
      let next = cursor.children.find((c) => c.name === part)
      if (!next) {
        next = {
          name: part,
          path: parts.slice(0, idx + 1).join('/'),
          isDir: isLast ? file.is_dir : true,
          size: isLast ? file.size : 0,
          children: [],
        }
        cursor.children.push(next)
      }
      cursor = next
    })
  }
  return root
}

export function SkillPackageTree({ files, onSelect, selectedPath }: SkillPackageTreeProps) {
  const tree = useMemo(() => buildTree(files), [files])
  return (
    <ul className="space-y-0.5 text-xs font-mono">
      {tree.children.map((child) => (
        <TreeItem
          key={child.path}
          node={child}
          depth={0}
          onSelect={onSelect}
          selectedPath={selectedPath}
        />
      ))}
    </ul>
  )
}

function TreeItem({
  node,
  depth,
  onSelect,
  selectedPath,
}: {
  node: TreeNode
  depth: number
  onSelect?: (path: string) => void
  selectedPath?: string | null
}) {
  const isSelected = selectedPath === node.path
  return (
    <li>
      <button
        type="button"
        onClick={() => !node.isDir && onSelect?.(node.path)}
        className={`flex w-full items-center gap-1 rounded px-1 py-0.5 text-left ${
          isSelected ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'
        } ${isSelected ? '' : node.isDir ? 'text-foreground/80' : 'text-foreground'}`}
        style={{ paddingLeft: depth * 12 + 4 }}
      >
        {node.isDir ? (
          <ChevronRight className="size-3 text-muted-foreground" />
        ) : (
          <FileText className="size-3 text-muted-foreground" />
        )}
        {node.isDir ? <Folder className="size-3" /> : null}
        <span>{node.name}</span>
        {!node.isDir && (
          <span className="ml-auto moldy-ui-micro text-muted-foreground">
            {node.size}b
          </span>
        )}
      </button>
      {node.isDir && node.children.length > 0 && (
        <ul className="space-y-0.5">
          {node.children.map((child) => (
            <TreeItem
              key={child.path}
              node={child}
              depth={depth + 1}
              onSelect={onSelect}
              selectedPath={selectedPath}
            />
          ))}
        </ul>
      )}
    </li>
  )
}
