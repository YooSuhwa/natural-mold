import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import {
  useTools,
  useCreateTool,
  useUpdateTool,
  useDeleteTool,
} from '@/lib/hooks/use-tools'
import { mockToolList } from '../../mocks/fixtures'

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
  return Wrapper
}

describe('useTools', () => {
  it('fetches tool list', async () => {
    const { result } = renderHook(() => useTools(), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual(mockToolList)
  })

  it('returns loading state initially', () => {
    const { result } = renderHook(() => useTools(), { wrapper: createWrapper() })
    expect(result.current.isLoading).toBe(true)
  })
})

describe('useCreateTool', () => {
  it('creates a tool and returns response', async () => {
    const wrapper = createWrapper()
    const { result } = renderHook(() => useCreateTool(), { wrapper })
    let response: unknown

    await act(async () => {
      response = await result.current.mutateAsync({
        definition_key: 'custom_http',
        name: 'My Tool',
        parameters: { api_url: 'https://api.example.com' },
      })
    })

    expect(response).toMatchObject({ id: 'tool-new', definition_key: 'custom_http' })
  })
})

describe('useUpdateTool', () => {
  it('updates the tool credential binding and returns the response', async () => {
    const wrapper = createWrapper()
    const { result } = renderHook(() => useUpdateTool(), { wrapper })
    let response: unknown

    await act(async () => {
      response = await result.current.mutateAsync({
        id: 'tool-1',
        data: { credential_id: 'cred-1' },
      })
    })

    expect(response).toMatchObject({ id: 'tool-1', credential_id: 'cred-1' })
  })
})

describe('useDeleteTool', () => {
  it('deletes a tool without error', async () => {
    const wrapper = createWrapper()
    const { result } = renderHook(() => useDeleteTool(), { wrapper })

    await act(async () => {
      await expect(result.current.mutateAsync('tool-1')).resolves.not.toThrow()
    })
  })
})
