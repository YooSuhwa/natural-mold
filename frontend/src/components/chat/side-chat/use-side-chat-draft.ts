'use client'

import { useEffect, useRef } from 'react'
import { useAui, useAuiState } from '@assistant-ui/react'
import type { ResourceContextReference } from '@/lib/chat/context/resource-context'
import { useRegisterUnsavedOrgDraft } from '@/lib/org-scope/unsaved-drafts'
import { useSideChat } from './side-chat-context'

/** Keep an unsent side draft when the responsive dialog mounts a new runtime. */
export function useSideChatDraft(
  conversationId: string | null,
  references: readonly ResourceContextReference[],
) {
  const aui = useAui()
  const workspace = useSideChat()
  const draft = workspace?.draft
  const isSide = Boolean(conversationId && conversationId === workspace?.sideId)
  const hasUnsentText = useAuiState(
    (state) => state.composer.isEditing && state.composer.text.trim() !== '',
  )
  useRegisterUnsavedOrgDraft(isSide && hasUnsentText)
  const latest = useRef(references)
  useEffect(() => {
    latest.current = references
  }, [references])
  useEffect(() => {
    if (!isSide || !draft) return
    if (draft.current) {
      aui.composer.setText(draft.current.text)
    }
    return () => {
      draft.current = { text: aui.composer.getState().text, references: latest.current }
    }
  }, [aui, draft, isSide])
}
