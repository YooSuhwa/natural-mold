import { renderHook, waitFor, act } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { ReactNode } from "react"
import {
  useTools,
  useCreateCustomTool,
  useRegisterMCPServer,
  useUpdateToolAuthConfig,
  useDeleteTool,
} from "@/lib/hooks/use-tools"
import { mockToolList } from "../../mocks/fixtures"

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

describe("useTools", () => {
  it("fetches tool list", async () => {
    const { result } = renderHook(() => useTools(), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual(mockToolList)
  })

  it("returns loading state initially", () => {
    const { result } = renderHook(() => useTools(), { wrapper: createWrapper() })
    expect(result.current.isLoading).toBe(true)
  })
})

describe("useCreateCustomTool", () => {
  it("creates a custom tool and returns response", async () => {
    const wrapper = createWrapper()
    const { result } = renderHook(() => useCreateCustomTool(), { wrapper })
    let response: unknown

    await act(async () => {
      response = await result.current.mutateAsync({
        name: "My Tool",
        api_url: "https://api.example.com",
      })
    })

    expect(response).toMatchObject({ id: "tool-new", type: "custom" })
  })
})

describe("useRegisterMCPServer", () => {
  it("registers an MCP server and returns response", async () => {
    const wrapper = createWrapper()
    const { result } = renderHook(() => useRegisterMCPServer(), { wrapper })
    let response: unknown

    await act(async () => {
      response = await result.current.mutateAsync({
        name: "Test Server",
        url: "http://localhost:9000",
      })
    })

    expect(response).toMatchObject({ id: "mcp-new" })
  })
})

describe("useUpdateToolAuthConfig", () => {
  it("updates auth config and returns response", async () => {
    const wrapper = createWrapper()
    const { result } = renderHook(() => useUpdateToolAuthConfig(), { wrapper })
    let response: unknown

    await act(async () => {
      response = await result.current.mutateAsync({
        id: "tool-1",
        authConfig: { api_key: "test-key" },
      })
    })

    expect(response).toMatchObject({ auth_config: { api_key: "***" } })
  })
})

describe("useDeleteTool", () => {
  it("deletes a tool without error", async () => {
    const wrapper = createWrapper()
    const { result } = renderHook(() => useDeleteTool(), { wrapper })

    await act(async () => {
      await expect(result.current.mutateAsync("tool-1")).resolves.not.toThrow()
    })
  })
})
