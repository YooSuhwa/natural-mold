'use client'

import { createContext } from 'react'
import type {
  AppendMessage,
  AttachmentAdapter,
  RunConfig,
  ThreadMessage,
} from '@assistant-ui/react'

export interface AgUiChatActions {
  readonly resolveMessageId?: (
    message: Pick<ThreadMessage, 'id' | 'metadata'>,
  ) => Promise<string | undefined>
  readonly getBranchRunConfig: (
    operation: 'edit' | 'regenerate',
    message: Pick<ThreadMessage, 'id' | 'metadata'>,
    existing?: RunConfig,
  ) => Promise<RunConfig>
  readonly queueDraft: (message: AppendMessage, steer: boolean) => Promise<void>
  readonly shouldQueue: boolean
  readonly canStopActiveRun?: boolean
  readonly attachmentAdapter?: AttachmentAdapter
  readonly stopActiveRun: () => Promise<void>
}

export const AgUiChatActionContext = createContext<AgUiChatActions | null>(null)
