import { http, HttpResponse } from "msw"
import {
  mockAgentList,
  mockAgent,
  mockModelList,
  mockModel,
  mockToolList,
  mockTool,
  mockMCPServer,
  mockTemplateList,
  mockTemplate,
  mockConversationList,
  mockConversation,
  mockMessageList,
  mockTriggerList,
  mockTrigger,
  mockUsageSummary,
  mockCreationSession,
  mockCreationMessageResult,
} from "./fixtures"

const API_BASE = "http://localhost:8001"

export const handlers = [
  // ── Agents ─────────────────────────────────────────────────────
  http.get(`${API_BASE}/api/agents`, () => {
    return HttpResponse.json(mockAgentList)
  }),

  http.get(`${API_BASE}/api/agents/:id`, ({ params }) => {
    if (params.id === "not-found") {
      return HttpResponse.json({ detail: "Agent not found" }, { status: 404 })
    }
    return HttpResponse.json({ ...mockAgent, id: params.id })
  }),

  http.post(`${API_BASE}/api/agents`, async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>
    return HttpResponse.json({
      ...mockAgent,
      id: "agent-new",
      name: body.name,
      description: body.description ?? null,
      system_prompt: body.system_prompt,
    })
  }),

  http.put(`${API_BASE}/api/agents/:id`, async ({ params, request }) => {
    const body = (await request.json()) as Record<string, unknown>
    return HttpResponse.json({
      ...mockAgent,
      id: params.id,
      ...body,
    })
  }),

  http.delete(`${API_BASE}/api/agents/:id`, () => {
    return new HttpResponse(null, { status: 204 })
  }),

  // ── Models ─────────────────────────────────────────────────────
  http.get(`${API_BASE}/api/models`, () => {
    return HttpResponse.json(mockModelList)
  }),

  http.post(`${API_BASE}/api/models`, async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>
    return HttpResponse.json({
      ...mockModel,
      id: "model-new",
      provider: body.provider,
      model_name: body.model_name,
      display_name: body.display_name,
    })
  }),

  http.delete(`${API_BASE}/api/models/:id`, () => {
    return new HttpResponse(null, { status: 204 })
  }),

  // ── Tools ──────────────────────────────────────────────────────
  http.get(`${API_BASE}/api/tools`, () => {
    return HttpResponse.json(mockToolList)
  }),

  http.post(`${API_BASE}/api/tools/custom`, async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>
    return HttpResponse.json({
      ...mockTool,
      id: "tool-new",
      type: "custom",
      is_system: false,
      name: body.name,
      api_url: body.api_url,
    })
  }),

  http.post(`${API_BASE}/api/tools/mcp-server`, async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>
    return HttpResponse.json({
      ...mockMCPServer,
      id: "mcp-new",
      name: body.name,
      url: body.url,
    })
  }),

  http.post(`${API_BASE}/api/tools/mcp-server/:serverId/test`, () => {
    return HttpResponse.json({ success: true, tools: [{ name: "test_tool" }] })
  }),

  http.patch(`${API_BASE}/api/tools/:id/auth-config`, async ({ params }) => {
    return HttpResponse.json({
      ...mockTool,
      id: params.id,
      auth_config: { api_key: "***" },
    })
  }),

  http.delete(`${API_BASE}/api/tools/:id`, () => {
    return new HttpResponse(null, { status: 204 })
  }),

  // ── Templates ──────────────────────────────────────────────────
  http.get(`${API_BASE}/api/templates`, () => {
    return HttpResponse.json(mockTemplateList)
  }),

  http.get(`${API_BASE}/api/templates/:id`, ({ params }) => {
    return HttpResponse.json({ ...mockTemplate, id: params.id })
  }),

  // ── Conversations ──────────────────────────────────────────────
  http.get(`${API_BASE}/api/agents/:agentId/conversations`, () => {
    return HttpResponse.json(mockConversationList)
  }),

  http.post(`${API_BASE}/api/agents/:agentId/conversations`, async ({ params, request }) => {
    const body = (await request.json()) as Record<string, unknown>
    return HttpResponse.json({
      ...mockConversation,
      id: "conv-new",
      agent_id: params.agentId,
      title: body.title ?? null,
    })
  }),

  http.get(`${API_BASE}/api/conversations/:id/messages`, () => {
    return HttpResponse.json(mockMessageList)
  }),

  // ── Triggers ───────────────────────────────────────────────────
  http.get(`${API_BASE}/api/agents/:agentId/triggers`, () => {
    return HttpResponse.json(mockTriggerList)
  }),

  http.post(`${API_BASE}/api/agents/:agentId/triggers`, async ({ params, request }) => {
    const body = (await request.json()) as Record<string, unknown>
    return HttpResponse.json({
      ...mockTrigger,
      id: "trigger-new",
      agent_id: params.agentId,
      trigger_type: body.trigger_type,
      schedule_config: body.schedule_config,
      input_message: body.input_message,
    })
  }),

  http.put(`${API_BASE}/api/agents/:agentId/triggers/:triggerId`, async ({ params, request }) => {
    const body = (await request.json()) as Record<string, unknown>
    return HttpResponse.json({
      ...mockTrigger,
      id: params.triggerId,
      agent_id: params.agentId,
      ...body,
    })
  }),

  http.delete(`${API_BASE}/api/agents/:agentId/triggers/:triggerId`, () => {
    return new HttpResponse(null, { status: 204 })
  }),

  // ── Usage ──────────────────────────────────────────────────────
  http.get(`${API_BASE}/api/agents/:agentId/usage`, () => {
    return HttpResponse.json({ total_tokens: 100000, estimated_cost: 0.85 })
  }),

  http.get(`${API_BASE}/api/usage/summary`, () => {
    return HttpResponse.json(mockUsageSummary)
  }),

  // ── Creation Session ───────────────────────────────────────────
  http.post(`${API_BASE}/api/agents/create-session`, () => {
    return HttpResponse.json(mockCreationSession)
  }),

  http.get(`${API_BASE}/api/agents/create-session/:id`, ({ params }) => {
    return HttpResponse.json({ ...mockCreationSession, id: params.id })
  }),

  http.post(`${API_BASE}/api/agents/create-session/:id/message`, () => {
    return HttpResponse.json(mockCreationMessageResult)
  }),

  http.post(`${API_BASE}/api/agents/create-session/:id/confirm`, () => {
    return HttpResponse.json({ ...mockAgent, id: "agent-from-session" })
  }),
]
