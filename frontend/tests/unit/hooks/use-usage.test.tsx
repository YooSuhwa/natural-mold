import { renderHook, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { ReactNode } from "react"
import { useUsageSummary } from "@/lib/hooks/use-usage"
import { mockUsageSummary } from "../../mocks/fixtures"

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

describe("useUsageSummary", () => {
  it("fetches usage summary", async () => {
    const { result } = renderHook(() => useUsageSummary(), {
      wrapper: createWrapper(),
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual(mockUsageSummary)
  })

  it("returns loading state initially", () => {
    const { result } = renderHook(() => useUsageSummary(), {
      wrapper: createWrapper(),
    })
    expect(result.current.isLoading).toBe(true)
  })

  it("accepts period parameter", async () => {
    const { result } = renderHook(() => useUsageSummary("30d"), {
      wrapper: createWrapper(),
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual(mockUsageSummary)
  })
})
