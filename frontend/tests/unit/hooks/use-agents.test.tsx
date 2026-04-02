import { renderHook, waitFor, act } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { ReactNode } from "react"
import { useAgents, useAgent, useCreateAgent, useUpdateAgent, useDeleteAgent } from "@/lib/hooks/use-agents"
import { mockAgentList, mockAgent } from "../../mocks/fixtures"

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

describe("useAgents", () => {
  it("fetches agent list", async () => {
    const { result } = renderHook(() => useAgents(), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual(mockAgentList)
  })

  it("returns loading state initially", () => {
    const { result } = renderHook(() => useAgents(), { wrapper: createWrapper() })
    expect(result.current.isLoading).toBe(true)
  })
})

describe("useAgent", () => {
  it("fetches a single agent by id", async () => {
    const { result } = renderHook(() => useAgent("agent-1"), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual({ ...mockAgent, id: "agent-1" })
  })

  it("does not fetch when id is empty string", () => {
    const { result } = renderHook(() => useAgent(""), { wrapper: createWrapper() })
    expect(result.current.fetchStatus).toBe("idle")
  })
})

describe("useCreateAgent", () => {
  it("creates an agent and returns response data", async () => {
    const wrapper = createWrapper()
    const { result } = renderHook(() => useCreateAgent(), { wrapper })
    let response: unknown

    await act(async () => {
      response = await result.current.mutateAsync({
        name: "New Agent",
        system_prompt: "Test prompt",
        model_id: "model-1",
      })
    })

    expect(response).toMatchObject({ id: "agent-new", name: "New Agent" })
  })
})

describe("useUpdateAgent", () => {
  it("updates an agent and returns response data", async () => {
    const wrapper = createWrapper()
    const { result } = renderHook(() => useUpdateAgent("agent-1"), { wrapper })
    let response: unknown

    await act(async () => {
      response = await result.current.mutateAsync({ name: "Updated Name" })
    })

    expect(response).toMatchObject({ id: "agent-1", name: "Updated Name" })
  })
})

describe("useDeleteAgent", () => {
  it("deletes an agent without error", async () => {
    const wrapper = createWrapper()
    const { result } = renderHook(() => useDeleteAgent(), { wrapper })

    await act(async () => {
      await expect(result.current.mutateAsync("agent-1")).resolves.not.toThrow()
    })
  })
})
