import { apiFetch } from './client'
import type { Tool, MCPServer, ToolCustomCreateRequest, MCPServerCreateRequest } from '@/lib/types'

export const toolsApi = {
  list: () => apiFetch<Tool[]>('/api/tools'),
  createCustom: (data: ToolCustomCreateRequest) =>
    apiFetch<Tool>('/api/tools/custom', { method: 'POST', body: JSON.stringify(data) }),
  registerMCPServer: (data: MCPServerCreateRequest) =>
    apiFetch<MCPServer>('/api/tools/mcp-server', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  testMCPConnection: (serverId: string) =>
    apiFetch<{ success: boolean; tools: unknown[]; error?: string }>(
      `/api/tools/mcp-server/${serverId}/test`,
      { method: 'POST' },
    ),
  updateAuthConfig: (
    id: string,
    authConfig: Record<string, unknown>,
    credentialId?: string | null,
  ) =>
    apiFetch<Tool>(`/api/tools/${id}/auth-config`, {
      method: 'PATCH',
      body: JSON.stringify({
        auth_config: authConfig,
        ...(credentialId !== undefined ? { credential_id: credentialId } : {}),
      }),
    }),
  delete: (id: string) => apiFetch<void>(`/api/tools/${id}`, { method: 'DELETE' }),
}
