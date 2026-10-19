'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { accessRequestsApi } from '@/lib/api/access-requests'
import { accessRequestQueryKeys } from '@/lib/query-keys/access-requests'
import { grantQueryKeys } from '@/lib/query-keys/grants'
import type {
  AccessRequestApprove,
  AccessRequestCreate,
  RequestBox,
} from '@/lib/types/access-request'

export function useAccessRequests(box: RequestBox, cursor: string | null) {
  return useQuery({
    queryKey: accessRequestQueryKeys.box(box, cursor),
    queryFn: () => accessRequestsApi.list(box, cursor),
  })
}

function useInvalidate() {
  const qc = useQueryClient()
  return () => qc.invalidateQueries({ queryKey: accessRequestQueryKeys.all })
}

export function useCreateAccessRequest() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: (body: AccessRequestCreate) => accessRequestsApi.create(body),
    onSuccess: invalidate,
  })
}

export type AccessDecision =
  | { id: string; action: 'approve'; body?: AccessRequestApprove }
  | { id: string; action: 'deny'; note?: string | null }
  | { id: string; action: 'cancel' }

export function useDecideAccessRequest() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (decision: AccessDecision) => {
      if (decision.action === 'approve')
        return accessRequestsApi.approve(decision.id, decision.body)
      if (decision.action === 'deny') return accessRequestsApi.deny(decision.id, decision.note)
      return accessRequestsApi.cancel(decision.id)
    },
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: accessRequestQueryKeys.all })
      await qc.invalidateQueries({ queryKey: grantQueryKeys.all }) // an approval adds a grant
    },
  })
}
