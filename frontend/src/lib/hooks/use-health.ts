'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { healthApi } from '@/lib/api/health'
import { healthQueryKeys } from '@/lib/query-keys/health'
import type { HealthCheckEntry, HealthTargetKind, RunHealthCheckInput } from '@/lib/types/health'
import { requiredQueryValue } from './required-query-value'

const DEFAULT_HISTORY_LIMIT = 30

/** Latest snapshot per model — drives the "Status" column on /models. */
export function useModelHealth() {
  return useQuery({
    queryKey: healthQueryKeys.models,
    queryFn: healthApi.listModels,
    // Health is volatile by definition. 60s is a fair compromise between
    // freshness and avoiding a request storm during heavy table renders.
    staleTime: 60_000,
  })
}

/** Latest snapshot per MCP server — drives the "Status" column on /mcp-servers. */
export function useMcpHealth() {
  return useQuery({
    queryKey: healthQueryKeys.mcpServers,
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
    queryKey: healthQueryKeys.history(targetKind, targetId, limit),
    queryFn: () =>
      healthApi.history(targetKind, requiredQueryValue(targetId, 'health target id'), limit),
    enabled: !!targetId,
    staleTime: 30_000,
  })
}

/**
 * Run a probe and refresh both the latest-snapshot list (so the table chip
 * updates) and the history feed (so the chart redraws with the new point).
 *
 * The mutation also writes the fresh entry directly into the list cache so
 * the chip updates synchronously — invalidate + refetch alone leaves a
 * brief staleness window where the prior "unhealthy" status flickers.
 */
export function useRunHealthCheck() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (input: RunHealthCheckInput) => healthApi.runCheck(input),
    onSuccess: (data, input) => {
      const listKey =
        input.targetKind === 'model' ? healthQueryKeys.models : healthQueryKeys.mcpServers
      // Optimistic write: replace this target's row with the fresh probe so
      // the UI reflects the new status immediately, before the refetch lands.
      qc.setQueryData<HealthCheckEntry[] | undefined>(listKey, (prev) => {
        if (!prev) return prev
        const idx = prev.findIndex((row) => row.target_id === data.target_id)
        const fresh = { ...data, name: idx >= 0 ? prev[idx].name : data.name }
        if (idx === -1) return [...prev, fresh]
        const next = prev.slice()
        next[idx] = fresh
        return next
      })
      qc.invalidateQueries({ queryKey: listKey })
      qc.invalidateQueries({
        queryKey: healthQueryKeys.history(input.targetKind, input.targetId),
      })
    },
  })
}
