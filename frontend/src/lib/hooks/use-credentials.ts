'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { credentialsApi } from '@/lib/api/credentials'
import type { Credential, CredentialCreateRequest, CredentialUpdateRequest } from '@/lib/types'

export function useCredentials() {
  return useQuery({ queryKey: ['credentials'], queryFn: credentialsApi.list, staleTime: 60000 })
}

/** 단일 credential lookup. id가 null/undefined면 항상 null 반환. */
export function useCredential(id: string | null | undefined): Credential | null {
  const { data } = useCredentials()
  if (!id) return null
  return data?.find((c) => c.id === id) ?? null
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
