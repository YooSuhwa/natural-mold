'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { workflowsApi } from '@/lib/api/workflows'
import { workflowQueryKeys } from '@/lib/query-keys/workflows'
import { RUN_TERMINAL, type WorkflowDefinition, type WorkflowDetail } from '@/lib/types/workflow'

export function useWorkflows() {
  return useQuery({ queryKey: workflowQueryKeys.list, queryFn: workflowsApi.list })
}

export function useWorkflow(id: string) {
  return useQuery({ queryKey: workflowQueryKeys.detail(id), queryFn: () => workflowsApi.get(id) })
}

/** Issues of one saved draft revision: a new revision is a new key, never a stale list. */
export function useWorkflowIssues(id: string, revision: number | undefined) {
  return useQuery({
    queryKey: workflowQueryKeys.issues(id, revision ?? 0),
    queryFn: () => workflowsApi.issues(id),
    enabled: revision !== undefined,
  })
}

export function useCreateWorkflow() {
  const qc = useQueryClient()
  return useMutation({
    // A template's definition is saved as the first draft right after creating.
    mutationFn: async ({ name, definition }: { name: string; definition?: WorkflowDefinition }) => {
      const created = await workflowsApi.create(name)
      if (!definition) return created
      return workflowsApi.saveDraft(created.id, { revision: created.draft_revision, definition })
    },
    onSettled: () => qc.invalidateQueries({ queryKey: workflowQueryKeys.list }),
  })
}

export function useSaveWorkflowDraft(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { revision: number; definition?: WorkflowDefinition; name?: string }) =>
      workflowsApi.saveDraft(id, body),
    onSuccess: (saved: WorkflowDetail) => {
      qc.setQueryData(workflowQueryKeys.detail(id), saved)
      void qc.invalidateQueries({ queryKey: workflowQueryKeys.list })
    },
  })
}

export function usePublishWorkflow(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (revision: number) => workflowsApi.publish(id, revision),
    onSettled: () => qc.invalidateQueries({ queryKey: workflowQueryKeys.all }),
  })
}

/** Published versions, newest first; a publish refreshes them (it invalidates ``all``). */
export function useWorkflowVersions(id: string) {
  return useQuery({
    queryKey: workflowQueryKeys.versions(id),
    queryFn: () => workflowsApi.versions(id),
  })
}

/** A published version (immutable: it never goes stale). */
export function useWorkflowVersion(id: string, versionNo: number | null | undefined) {
  return useQuery({
    queryKey: workflowQueryKeys.version(id, versionNo ?? 0),
    queryFn: () => workflowsApi.version(id, versionNo ?? 0),
    enabled: !!versionNo,
    staleTime: Infinity,
  })
}

export function useStartWorkflowRun(id: string) {
  return useMutation({
    mutationFn: (inputs: Record<string, unknown>) => workflowsApi.startRun(id, inputs),
  })
}

const RUN_POLL_MS = 3_000

export function useWorkflowRuns(id: string) {
  return useQuery({ queryKey: workflowQueryKeys.runs(id), queryFn: () => workflowsApi.runs(id) })
}

/** A run polls while it can still change (running, waiting, canceling). */
export function useWorkflowRun(runId: string) {
  return useQuery({
    queryKey: workflowQueryKeys.run(runId),
    queryFn: () => workflowsApi.run(runId),
    refetchInterval: (query) =>
      query.state.data && RUN_TERMINAL.includes(query.state.data.status) ? false : RUN_POLL_MS,
  })
}

export function useCancelWorkflowRun(runId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => workflowsApi.cancelRun(runId),
    onSuccess: (run) => qc.setQueryData(workflowQueryKeys.run(runId), run),
  })
}

export function useWorkflowsUsingAgent(agentId: string) {
  return useQuery({
    queryKey: workflowQueryKeys.usingAgent(agentId),
    queryFn: () => workflowsApi.usingAgent(agentId),
  })
}
