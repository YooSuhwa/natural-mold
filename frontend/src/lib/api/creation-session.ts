import { apiFetch } from "./client"
import type { Agent, CreationSession } from "@/lib/types"

export const creationSessionApi = {
  start: () =>
    apiFetch<CreationSession>("/api/agents/create-session", { method: "POST" }),
  get: (id: string) => apiFetch<CreationSession>(`/api/agents/create-session/${id}`),
  sendMessage: (id: string, content: string) =>
    apiFetch<{ role: string; content: string; draft_config: unknown | null }>(
      `/api/agents/create-session/${id}/message`,
      { method: "POST", body: JSON.stringify({ content }) },
    ),
  confirm: (id: string) =>
    apiFetch<Agent>(`/api/agents/create-session/${id}/confirm`, { method: "POST" }),
}
