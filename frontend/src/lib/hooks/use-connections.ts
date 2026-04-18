'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { connectionsApi, type ListConnectionsParams } from '@/lib/api/connections'
import type {
  ConnectionCreateRequest,
  ConnectionType,
  ConnectionUpdateRequest,
} from '@/lib/types'

type ConnectionScope = { type: ConnectionType; provider_name: string }

function scopeKey(scope: ConnectionScope) {
  return ['connections', scope.type, scope.provider_name] as const
}

export function useConnections(params: ListConnectionsParams = {}) {
  const key = ['connections', params.type ?? 'all', params.provider_name ?? 'all'] as const
  return useQuery({
    queryKey: key,
    queryFn: () => connectionsApi.list(params),
    staleTime: 60_000,
  })
}

export function useCreateConnection() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: ConnectionCreateRequest) => connectionsApi.create(data),
    onSuccess: (created) => {
      // Scope 전체 invalidation — is_default 승격 시 같은 scope의 다른 connection이
      // 함께 false로 뒤집히므로 단일 id 캐시 갱신으로는 불충분.
      qc.invalidateQueries({
        queryKey: scopeKey({ type: created.type, provider_name: created.provider_name }),
      })
      qc.invalidateQueries({ queryKey: ['connections'] })
    },
  })
}

export function useUpdateConnection() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: ConnectionUpdateRequest }) =>
      connectionsApi.update(id, data),
    onSuccess: (updated) => {
      qc.invalidateQueries({
        queryKey: scopeKey({ type: updated.type, provider_name: updated.provider_name }),
      })
      qc.invalidateQueries({ queryKey: ['connections'] })
    },
  })
}

export function useDeleteConnection() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id }: { id: string; type: ConnectionType; provider_name: string }) =>
      connectionsApi.delete(id),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({
        queryKey: scopeKey({ type: variables.type, provider_name: variables.provider_name }),
      })
      qc.invalidateQueries({ queryKey: ['connections'] })
    },
  })
}
