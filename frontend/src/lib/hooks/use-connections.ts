'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { connectionsApi, type ListConnectionsParams } from '@/lib/api/connections'
import type {
  Connection,
  ConnectionCreateRequest,
  ConnectionType,
  ConnectionUpdateRequest,
} from '@/lib/types'

type ConnectionScope = { type: ConnectionType; provider_name: string }

export function scopeKey(scope: ConnectionScope) {
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
      // Seed the owning scope cache synchronously so the next read (e.g.
      // `resolveCustomConnectionId`'s find-or-create) sees it before the
      // async refetch below completes. Prevents duplicate CUSTOM connections
      // on rapid dual submits (ADR-008 N:1).
      const scopeK = scopeKey({ type: created.type, provider_name: created.provider_name })
      qc.setQueryData<Connection[]>(scopeK, (prev) => {
        if (!prev) return prev
        if (prev.some((c) => c.id === created.id)) return prev
        return [...prev, created]
      })
      // is_default 승격은 같은 scope의 다른 connection을 false로 뒤집으므로
      // 단일 id 갱신으로는 불충분. `['connections']` prefix invalidate가
      // scopeK를 포함하므로 scopeK 별도 호출은 중복.
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
