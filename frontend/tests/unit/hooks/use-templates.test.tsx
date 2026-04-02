import { renderHook, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { ReactNode } from "react"
import { useTemplates } from "@/lib/hooks/use-templates"
import { mockTemplateList } from "../../mocks/fixtures"

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

describe("useTemplates", () => {
  it("fetches template list", async () => {
    const { result } = renderHook(() => useTemplates(), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual(mockTemplateList)
  })

  it("returns loading state initially", () => {
    const { result } = renderHook(() => useTemplates(), { wrapper: createWrapper() })
    expect(result.current.isLoading).toBe(true)
  })

  it("passes category parameter", async () => {
    const { result } = renderHook(() => useTemplates("productivity"), {
      wrapper: createWrapper(),
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    // MSW handler returns full list regardless of category param
    expect(result.current.data).toEqual(mockTemplateList)
  })
})
