'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toolsApi } from '@/lib/api/tools'
import type { ToolCreateRequest, ToolPatchRequest } from '@/lib/types/tool'

const KEY_LIST = ['tools'] as const
const KEY_TYPES = ['tool-types'] as const

export function useToolTypes() {
  return useQuery({
    queryKey: KEY_TYPES,
    queryFn: toolsApi.listTypes,
    staleTime: 5 * 60_000,
  })
}

export function useToolType(key: string | null | undefined) {
  return useQuery({
    queryKey: ['tool-types', key],
    queryFn: () => toolsApi.getType(key!),
    enabled: !!key,
    staleTime: 5 * 60_000,
  })
}

export function useTools(params?: { definition_key?: string; enabled?: boolean }) {
  return useQuery({
    queryKey: ['tools', params ?? {}],
    queryFn: () => toolsApi.list(params),
    staleTime: 30_000,
  })
}

export function useTool(id: string | null | undefined) {
  return useQuery({
    queryKey: ['tools', id],
    queryFn: () => toolsApi.get(id!),
    enabled: !!id,
  })
}

export function useCreateTool() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: ToolCreateRequest) => toolsApi.create(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY_LIST }),
  })
}

export function useUpdateTool() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: ToolPatchRequest }) =>
      toolsApi.update(id, data),
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({ queryKey: KEY_LIST })
      qc.invalidateQueries({ queryKey: ['tools', id] })
    },
  })
}

export function useDeleteTool() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => toolsApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY_LIST }),
  })
}

export function useRunTool() {
  return useMutation({
    mutationFn: ({ id, runtime_args }: { id: string; runtime_args?: Record<string, unknown> }) =>
      toolsApi.run(id, runtime_args ?? {}),
  })
}
