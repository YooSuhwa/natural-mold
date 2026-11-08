import {
  BotIcon,
  CircleXIcon,
  FlagIcon,
  GitBranchIcon,
  PlayIcon,
  ShieldCheckIcon,
  ShuffleIcon,
  type LucideIcon,
} from 'lucide-react'

import type { WorkflowNodeType } from '@/lib/types/workflow'

import type { AddableType } from './editor-commands'

export const NODE_ICONS: Record<WorkflowNodeType, LucideIcon> = {
  start: PlayIcon,
  agent: BotIcon,
  decision_bool: GitBranchIcon,
  approval: ShieldCheckIcon,
  transform: ShuffleIcon,
  output: FlagIcon,
  fail: CircleXIcon,
}

/** The order node kinds are offered in the add dialog (agents get their own list). */
export const ADDABLE_KINDS: readonly AddableType[] = [
  'decision_bool',
  'approval',
  'transform',
  'output',
  'fail',
]
