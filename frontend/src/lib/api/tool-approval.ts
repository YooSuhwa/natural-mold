import { apiFetch } from './client'
import type { ToolApprovalView, ToolGrant } from '@/lib/types/tool-approval'

const grants = (conversationId: string) =>
  `/api/conversations/${encodeURIComponent(conversationId)}/tool-grants`

export const toolApprovalApi = {
  view: (agentId: string) =>
    apiFetch<ToolApprovalView>(`/api/agents/${encodeURIComponent(agentId)}/tool-approval`),
  grants: (conversationId: string) => apiFetch<ToolGrant[]>(grants(conversationId)),
  grant: (conversationId: string, tool: string) =>
    apiFetch<ToolGrant>(grants(conversationId), { method: 'POST', body: JSON.stringify({ tool }) }),
  revoke: (conversationId: string, tool: string) =>
    apiFetch<void>(`${grants(conversationId)}/${encodeURIComponent(tool)}`, { method: 'DELETE' }),
}
