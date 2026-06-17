'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toolsApi } from '@/lib/api/tools'
import { toolQueryKeys, type ToolListQueryParams } from '@/lib/query-keys/tools'
import type { ToolCreateRequest, ToolPatchRequest } from '@/lib/types/tool'
import { requiredQueryValue } from './required-query-value'

export function useToolTypes() {
  return useQuery({
    queryKey: toolQueryKeys.types,
    queryFn: toolsApi.listTypes,
    staleTime: 5 * 60_000,
  })
}

export function useToolType(key: string | null | undefined) {
  return useQuery({
    queryKey: toolQueryKeys.typeDetail(key),
    queryFn: () => toolsApi.getType(requiredQueryValue(key, 'tool type key')),
    enabled: !!key,
    staleTime: 5 * 60_000,
  })
}

export function useTools(params?: ToolListQueryParams) {
  return useQuery({
    queryKey: toolQueryKeys.list(params),
    queryFn: () => toolsApi.list(params),
    staleTime: 30_000,
  })
}

export function useTool(id: string | null | undefined) {
  return useQuery({
    queryKey: toolQueryKeys.detail(id),
    queryFn: () => toolsApi.get(requiredQueryValue(id, 'tool id')),
    enabled: !!id,
  })
}

export function useCreateTool() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: ToolCreateRequest) => toolsApi.create(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: toolQueryKeys.all }),
  })
}

export function useUpdateTool() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: ToolPatchRequest }) => toolsApi.update(id, data),
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({ queryKey: toolQueryKeys.all })
      qc.invalidateQueries({ queryKey: toolQueryKeys.detail(id) })
    },
  })
}

export function useDeleteTool() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => toolsApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: toolQueryKeys.all }),
  })
}

export function useRunTool() {
  return useMutation({
    mutationFn: ({ id, runtime_args }: { id: string; runtime_args?: Record<string, unknown> }) =>
      toolsApi.run(id, runtime_args ?? {}),
  })
}
