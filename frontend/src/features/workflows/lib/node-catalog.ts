import {
  BellIcon,
  BotIcon,
  CircleXIcon,
  ColumnsIcon,
  FlagIcon,
  GitBranchIcon,
  GitForkIcon,
  MessageCircleQuestionIcon,
  PlayIcon,
  RepeatIcon,
  ShieldCheckIcon,
  ShuffleIcon,
  SparklesIcon,
  WrenchIcon,
  type LucideIcon,
} from 'lucide-react'

import { FIXED_EXITS, type EdgeWhen, type WorkflowNodeType } from '@/lib/types/workflow'

import type { AddableType } from './editor-commands'

export const NODE_ICONS: Record<WorkflowNodeType, LucideIcon> = {
  start: PlayIcon,
  agent: BotIcon,
  decision_bool: GitBranchIcon,
  approval: ShieldCheckIcon,
  transform: ShuffleIcon,
  output: FlagIcon,
  fail: CircleXIcon,
  switch: GitForkIcon,
  llm: SparklesIcon,
  ask: MessageCircleQuestionIcon,
  notify: BellIcon,
  for_each: RepeatIcon,
  parallel: ColumnsIcon,
  tool: WrenchIcon,
}

/** The order node kinds are offered in the add dialog (agents get their own list). */
export const ADDABLE_KINDS: readonly AddableType[] = [
  'decision_bool',
  'switch',
  'llm',
  'for_each',
  'parallel',
  'tool',
  'approval',
  'ask',
  'notify',
  'transform',
  'output',
  'fail',
]

/** An exit's label: a fixed exit's translation, or a switch case's own name. */
export function branchLabel(t: (key: string) => string, when: EdgeWhen): string {
  return (FIXED_EXITS as readonly string[]).includes(when) ? t(`branch.${when}`) : when
}
