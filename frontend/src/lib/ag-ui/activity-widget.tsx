'use client'

import { createElement, useEffect, useState, type ReactNode } from 'react'
import { useAui, useAuiState } from '@assistant-ui/react'
import { apiFetch } from '@/lib/api/client'
import { isMcpAppUuid, projectMcpApp } from '@/lib/chat/mcp-apps/metadata'
import { withOrgScopeSignal } from '@/lib/org-scope/org-scope-signal'
import { useMoldyAgUiActivities } from './activity-context'
import { findWidgetBinding, type WidgetBinding } from './activity-widget-binding'

type ActivityScope = NonNullable<ReturnType<typeof useMoldyAgUiActivities>>

interface WidgetFallbackProps {
  readonly children: ReactNode
  readonly toolUI: ReactNode
}

/** Keep private widget output inside this mounted viewer, outside the agent and the replay cache. */
function AuthorizedWidget({
  path,
  binding,
  children,
}: {
  readonly path: string
  readonly binding: WidgetBinding
  readonly children: ReactNode
}) {
  const [artifact, setArtifact] = useState<unknown>(null)
  const aui = useAui()
  const part = useAuiState((s) => s.part)
  const Renderer = useAuiState((s) => s.tools.mcpApp?.render)

  useEffect(() => {
    const orgSignal = withOrgScopeSignal(path)
    let request: AbortController | undefined
    function load() {
      request?.abort()
      if (orgSignal?.aborted) return
      const controller = new AbortController()
      request = controller
      void apiFetch<unknown>(path, { method: 'GET', cache: 'no-store', signal: controller.signal })
        .then((value) => {
          if (!controller.signal.aborted && !orgSignal?.aborted) setArtifact(value)
        })
        .catch(() => {
          if (!controller.signal.aborted) setArtifact(null)
        })
    }
    function discard() {
      request?.abort()
      setArtifact(null)
    }
    function refreshWhenVisible() {
      if (document.visibilityState !== 'visible') return
      discard()
      load()
    }
    function restorePage(event: PageTransitionEvent) {
      if (event.persisted) refreshWhenVisible()
    }
    load()
    orgSignal?.addEventListener('abort', discard)
    document.addEventListener('visibilitychange', refreshWhenVisible)
    window.addEventListener('pageshow', restorePage)
    return () => {
      request?.abort()
      orgSignal?.removeEventListener('abort', discard)
      document.removeEventListener('visibilitychange', refreshWhenVisible)
      window.removeEventListener('pageshow', restorePage)
    }
  }, [path])

  if (!Renderer || part.type !== 'tool-call' || part.toolCallId !== binding.publicToolCallId)
    return children
  const verified = projectMcpApp(artifact, binding.toolCallId, part.result, binding)
  if (!verified) return children
  return createElement(Renderer, {
    ...part,
    mcp: { app: verified.app },
    result: verified.output,
    addResult: aui.part.addToolResult,
    resume: aui.part.resumeToolCall,
    respondToApproval: aui.part.respondToToolApproval,
  })
}

function ScopedWidget({
  scope,
  toolUI,
  children,
}: WidgetFallbackProps & { readonly scope: ActivityScope }) {
  const message = useAuiState((s) => s.message)
  const part = useAuiState((s) => s.part)
  if (part.type !== 'tool-call') return children
  // Only the scoped GET may authorize an app renderer, including on restored tool results.
  const fallback = part.mcp?.app ? children : (toolUI ?? children)
  if (!isMcpAppUuid(scope.conversationId) || message.role !== 'assistant') return fallback
  const custom = message.metadata.custom
  const agui = custom.agui
  const binding = findWidgetBinding(
    {
      messageId: message.id,
      publicMessageId:
        typeof custom.publicMessageId === 'string' ? custom.publicMessageId : undefined,
      wireMessageId:
        typeof agui === 'object' &&
        agui !== null &&
        'messageId' in agui &&
        typeof agui.messageId === 'string'
          ? agui.messageId
          : undefined,
      toolParentId: part.parentId,
      toolCallId: part.toolCallId,
    },
    scope.activities,
    scope.links,
  )
  if (!binding) return fallback
  const path = `/api/conversations/${scope.conversationId}/runs/${binding.runId}/mcp-apps/${encodeURIComponent(binding.toolCallId)}?binding_id=${binding.bindingId}`
  return (
    <AuthorizedWidget key={path} path={path} binding={binding}>
      {fallback}
    </AuthorizedWidget>
  )
}

export function MoldyAgUiBoundTool({ toolUI, children }: WidgetFallbackProps) {
  const scope = useMoldyAgUiActivities()
  return scope ? (
    <ScopedWidget scope={scope} toolUI={toolUI}>
      {children}
    </ScopedWidget>
  ) : (
    (toolUI ?? children)
  )
}
