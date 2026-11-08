'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { workflowsApi } from '@/lib/api/workflows'
import { workflowQueryKeys } from '@/lib/query-keys/workflows'
import type { WorkflowDefinition, WorkflowDetail } from '@/lib/types/workflow'

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
    mutationFn: (name: string) => workflowsApi.create(name),
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
