import { apiFetch } from "./client"
import type { Agent, AgentCreateRequest, AgentUpdateRequest } from "@/lib/types"

export const agentsApi = {
  list: () => apiFetch<Agent[]>("/api/agents"),
  get: (id: string) => apiFetch<Agent>(`/api/agents/${id}`),
  create: (data: AgentCreateRequest) =>
    apiFetch<Agent>("/api/agents", { method: "POST", body: JSON.stringify(data) }),
  update: (id: string, data: AgentUpdateRequest) =>
    apiFetch<Agent>(`/api/agents/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  delete: (id: string) =>
    apiFetch<void>(`/api/agents/${id}`, { method: "DELETE" }),
  toggleFavorite: (id: string) =>
    apiFetch<Agent>(`/api/agents/${id}/favorite`, { method: "PATCH" }),
}
