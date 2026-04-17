'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toolsApi } from '@/lib/api/tools'
import type { MCPServerCreateRequest, ToolCustomCreateRequest } from '@/lib/types'

export function useTools() {
  return useQuery({ queryKey: ['tools'], queryFn: toolsApi.list, staleTime: 60000 })
}

export function useCreateCustomTool() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: ToolCustomCreateRequest) => toolsApi.createCustom(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tools'] }),
  })
}

export function useRegisterMCPServer() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: MCPServerCreateRequest) => toolsApi.registerMCPServer(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tools'] }),
  })
}

export function useUpdateToolAuthConfig() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      id,
      authConfig,
      credentialId,
    }: {
      id: string
      authConfig: Record<string, unknown>
      credentialId?: string | null
    }) => toolsApi.updateAuthConfig(id, authConfig, credentialId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tools'] }),
  })
}

export function useDeleteTool() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => toolsApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tools'] }),
  })
}
