import { apiFetch } from "./client"
import type { Tool, MCPServer, ToolCustomCreateRequest, MCPServerCreateRequest } from "@/lib/types"

export const toolsApi = {
  list: () => apiFetch<Tool[]>("/api/tools"),
  createCustom: (data: ToolCustomCreateRequest) =>
    apiFetch<Tool>("/api/tools/custom", { method: "POST", body: JSON.stringify(data) }),
  registerMCPServer: (data: MCPServerCreateRequest) =>
    apiFetch<MCPServer>("/api/tools/mcp-server", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  testMCPConnection: (serverId: string) =>
    apiFetch<{ success: boolean; tools: unknown[]; error?: string }>(
      `/api/tools/mcp-server/${serverId}/test`,
      { method: "POST" },
    ),
  delete: (id: string) =>
    apiFetch<void>(`/api/tools/${id}`, { method: "DELETE" }),
}
