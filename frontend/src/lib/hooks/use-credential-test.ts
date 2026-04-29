'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { credentialsApi } from '@/lib/api/credentials'
import type { PreviewTestRequest } from '@/lib/types/credential'

export function useTestCredential() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => credentialsApi.test(id),
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: ['credentials'] })
      qc.invalidateQueries({ queryKey: ['credentials', id] })
      qc.invalidateQueries({ queryKey: ['credential-audit-logs', id] })
    },
  })
}

export function usePreviewTestCredential() {
  return useMutation({
    mutationFn: (data: PreviewTestRequest) => credentialsApi.previewTest(data),
  })
}

export function useStartOAuth2() {
  return useMutation({
    mutationFn: (id: string) => credentialsApi.startOAuth2(id),
  })
}
