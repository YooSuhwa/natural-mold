import { renderHook, waitFor, act } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { ReactNode } from "react"
import { useModels, useCreateModel, useDeleteModel } from "@/lib/hooks/use-models"
import { mockModelList } from "../../mocks/fixtures"

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

describe("useModels", () => {
  it("fetches model list", async () => {
    const { result } = renderHook(() => useModels(), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual(mockModelList)
  })

  it("returns loading state initially", () => {
    const { result } = renderHook(() => useModels(), { wrapper: createWrapper() })
    expect(result.current.isLoading).toBe(true)
  })
})

describe("useCreateModel", () => {
  it("creates a model and returns response", async () => {
    const wrapper = createWrapper()
    const { result } = renderHook(() => useCreateModel(), { wrapper })
    let response: unknown

    await act(async () => {
      response = await result.current.mutateAsync({
        provider: "openai",
        model_name: "gpt-4o-mini",
        display_name: "GPT-4o Mini",
      })
    })

    expect(response).toMatchObject({ id: "model-new", provider: "openai" })
  })
})

describe("useDeleteModel", () => {
  it("deletes a model without error", async () => {
    const wrapper = createWrapper()
    const { result } = renderHook(() => useDeleteModel(), { wrapper })

    await act(async () => {
      await expect(result.current.mutateAsync("model-1")).resolves.not.toThrow()
    })
  })
})
