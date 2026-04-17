'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { credentialsApi } from '@/lib/api/credentials'
import type { CredentialCreateRequest, CredentialUpdateRequest } from '@/lib/types'

export function useCredentials() {
  return useQuery({ queryKey: ['credentials'], queryFn: credentialsApi.list, staleTime: 60000 })
}

export function useCredentialProviders() {
  return useQuery({
    queryKey: ['credential-providers'],
    queryFn: credentialsApi.getProviders,
    staleTime: 300000,
  })
}

export function useCreateCredential() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: CredentialCreateRequest) => credentialsApi.create(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['credentials'] }),
  })
}

export function useUpdateCredential() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: CredentialUpdateRequest }) =>
      credentialsApi.update(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['credentials'] }),
  })
}

export function useDeleteCredential() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => credentialsApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['credentials'] })
      qc.invalidateQueries({ queryKey: ['tools'] })
    },
  })
}
