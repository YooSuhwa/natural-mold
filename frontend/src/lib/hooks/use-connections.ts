'use client'

import { useCallback } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { connectionsApi, type ListConnectionsParams } from '@/lib/api/connections'
import {
  CUSTOM_CONNECTION_PROVIDER_NAME,
  type Connection,
  type ConnectionCreateRequest,
  type ConnectionType,
  type ConnectionUpdateRequest,
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

// POST /api/connections/{id}/discover-tools — MCP 서버에서 tool 목록 discovery +
// Tool 레코드 upsert (M6.1 M7). 성공 시 ['tools'] + ['connections'] 무효화.
export function useDiscoverMcpTools() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => connectionsApi.discoverTools(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tools'] })
      qc.invalidateQueries({ queryKey: ['connections'] })
    },
  })
}

// ADR-008 N:1: 같은 credential을 쓰는 custom connection이 있으면 재사용하고,
// 없을 때만 새로 만든다. add-tool-dialog와 connection-binding-dialog가 동일
// 패턴을 쓰므로 단일 훅으로 집중해 drift 차단.
export function useFindOrCreateCustomConnection() {
  const qc = useQueryClient()
  const createConnection = useCreateConnection()

  const run = useCallback(
    async (credentialId: string, displayName: string): Promise<Connection> => {
      const cached = qc.getQueryData<Connection[]>(
        scopeKey({ type: 'custom', provider_name: CUSTOM_CONNECTION_PROVIDER_NAME }),
      )
      const existing = cached?.find((c) => c.credential_id === credentialId)
      if (existing) return existing

      return createConnection.mutateAsync({
        type: 'custom',
        provider_name: CUSTOM_CONNECTION_PROVIDER_NAME,
        display_name: displayName,
        credential_id: credentialId,
      })
    },
    [qc, createConnection],
  )

  return { run, isPending: createConnection.isPending }
}
