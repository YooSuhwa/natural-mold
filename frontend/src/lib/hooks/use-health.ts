'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { healthApi } from '@/lib/api/health'
import type { HealthTargetKind, RunHealthCheckInput } from '@/lib/types/health'

const KEY_MODELS = ['health', 'models'] as const
const KEY_MCP = ['health', 'mcp-servers'] as const

const DEFAULT_HISTORY_LIMIT = 30

/** Latest snapshot per model — drives the "Status" column on /models. */
export function useModelHealth() {
  return useQuery({
    queryKey: KEY_MODELS,
    queryFn: healthApi.listModels,
    // Health is volatile by definition. 60s is a fair compromise between
    // freshness and avoiding a request storm during heavy table renders.
    staleTime: 60_000,
  })
}

/** Latest snapshot per MCP server — drives the "Status" column on /mcp-servers. */
export function useMcpHealth() {
  return useQuery({
    queryKey: KEY_MCP,
    queryFn: healthApi.listMcpServers,
    staleTime: 60_000,
  })
}

/**
 * Time series for a single target. `enabled` so the query is skipped while
 * the parent hasn't selected a row yet (e.g. the detail sheet is closed).
 */
export function useHealthHistory(
  targetKind: HealthTargetKind,
  targetId: string | null | undefined,
  limit: number = DEFAULT_HISTORY_LIMIT,
) {
  return useQuery({
    queryKey: ['health', 'history', targetKind, targetId, limit] as const,
    queryFn: () => healthApi.history(targetKind, targetId!, limit),
    enabled: !!targetId,
    staleTime: 30_000,
  })
}

/**
 * Run a probe and refresh both the latest-snapshot list (so the table chip
 * updates) and the history feed (so the chart redraws with the new point).
 */
export function useRunHealthCheck() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (input: RunHealthCheckInput) => healthApi.runCheck(input),
    onSuccess: (_data, input) => {
      const listKey = input.targetKind === 'model' ? KEY_MODELS : KEY_MCP
      qc.invalidateQueries({ queryKey: listKey })
      qc.invalidateQueries({
        queryKey: ['health', 'history', input.targetKind, input.targetId],
      })
    },
  })
}
