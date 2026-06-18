'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { credentialsApi, systemCredentialsApi } from '@/lib/api/credentials'
import { credentialQueryKeys } from '@/lib/query-keys/credentials'
import { toolQueryKeys } from '@/lib/query-keys/tools'
import type { CredentialCreateRequest, CredentialUpdateRequest } from '@/lib/types/credential'
import { requiredQueryValue } from './required-query-value'

export function useCredentialTypes() {
  return useQuery({
    queryKey: credentialQueryKeys.types,
    queryFn: credentialsApi.listTypes,
    staleTime: 5 * 60_000,
  })
}

export function useCredentialType(key: string | null | undefined) {
  return useQuery({
    queryKey: credentialQueryKeys.typeDetail(key),
    queryFn: () => credentialsApi.getType(requiredQueryValue(key, 'credential type key')),
    enabled: !!key,
    staleTime: 5 * 60_000,
  })
}

export function useCredentials() {
  return useQuery({
    queryKey: credentialQueryKeys.all,
    queryFn: credentialsApi.list,
    staleTime: 30_000,
  })
}

export function useCredential(id: string | null | undefined) {
  return useQuery({
    queryKey: credentialQueryKeys.detail(id),
    queryFn: () => credentialsApi.get(requiredQueryValue(id, 'credential id')),
    enabled: !!id,
  })
}

export function useCreateCredential() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: CredentialCreateRequest) => credentialsApi.create(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: credentialQueryKeys.all }),
  })
}

export function useUpdateCredential() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: CredentialUpdateRequest }) =>
      credentialsApi.update(id, data),
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({ queryKey: credentialQueryKeys.all })
      qc.invalidateQueries({ queryKey: credentialQueryKeys.detail(id) })
    },
  })
}

export function useDeleteCredential() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => credentialsApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: credentialQueryKeys.all })
      qc.invalidateQueries({ queryKey: toolQueryKeys.all })
    },
  })
}

export function useCredentialAuditLogs(id: string | null | undefined, limit = 50) {
  return useQuery({
    queryKey: credentialQueryKeys.auditLogs(id, limit),
    queryFn: () => credentialsApi.listAuditLogs(requiredQueryValue(id, 'credential id'), limit),
    enabled: !!id,
  })
}

// -- System credentials (operator-managed) ----------------------------------

export function useSystemCredentials() {
  return useQuery({
    queryKey: credentialQueryKeys.systemAll,
    queryFn: systemCredentialsApi.list,
    staleTime: 30_000,
  })
}

export function useCreateSystemCredential() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: CredentialCreateRequest) => systemCredentialsApi.create(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: credentialQueryKeys.systemAll }),
  })
}

export function useUpdateSystemCredential() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: CredentialUpdateRequest }) =>
      systemCredentialsApi.update(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: credentialQueryKeys.systemAll }),
  })
}

export function useDeleteSystemCredential() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => systemCredentialsApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: credentialQueryKeys.systemAll }),
  })
}
