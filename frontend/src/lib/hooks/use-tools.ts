'use client'

import { useMemo } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toolsApi } from '@/lib/api/tools'
import type { Connection, Tool, ToolCustomCreateRequest } from '@/lib/types'

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

export function useDeleteTool() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => toolsApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tools'] }),
  })
}

// PATCH /api/tools/{id} — connection_id single-field (M6.1 옵션 D). CUSTOM first-bind /
// MCP re-wire / None 해제. PREBUILT는 서버가 400 — 호출부 가드 필요.
//
// Agent 응답의 ToolBrief는 {id, name}만 포함하고 connection 메타를 노출하지 않으므로
// ['agents'] invalidate는 불필요한 광역 refetch(대시보드/detail/conversations 전부).
// agent에서 tool connection 상태가 필요한 컴포넌트는 ['connections'] 또는 ['tools']를
// 직접 구독한다.
export function useUpdateTool() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: { connection_id?: string | null } }) =>
      toolsApi.update(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tools'] })
    },
  })
}

/**
 * Connection 기준으로 사용 중 tool 목록을 파생 — /connections 페이지 카드의
 * 카운트와 삭제 가드에 사용.
 * - PREBUILT: tool.provider_name 매칭. system tool은 `tool.connection_id`를 갖지 않고
 *   `user_id + type + provider_name` default connection이 SOT (M3 invariant).
 * - CUSTOM / MCP: tool.connection_id 매칭 (M6.1 SOT).
 */
export function useToolsByConnection(connection: Connection): Tool[] {
  const { data: tools } = useTools()
  return useMemo(() => {
    if (!tools) return []
    if (connection.type === 'prebuilt') {
      // PREBUILT runtime은 provider별 default connection만 사용. non-default row는
      // 어떤 tool에도 attach되지 않으므로 사용량 0 — 삭제 가드도 풀린다.
      if (!connection.is_default) return []
      return tools.filter(
        (t) => t.type === 'prebuilt' && t.provider_name === connection.provider_name,
      )
    }
    return tools.filter((t) => t.connection_id === connection.id)
  }, [tools, connection])
}
