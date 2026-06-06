'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { credentialsApi, systemCredentialsApi } from '@/lib/api/credentials'
import type { CredentialCreateRequest, CredentialUpdateRequest } from '@/lib/types/credential'

const KEY_LIST = ['credentials'] as const
const KEY_TYPES = ['credential-types'] as const
const KEY_SYSTEM_LIST = ['system-credentials'] as const

export function useCredentialTypes() {
  return useQuery({
    queryKey: KEY_TYPES,
    queryFn: credentialsApi.listTypes,
    staleTime: 5 * 60_000,
  })
}

export function useCredentialType(key: string | null | undefined) {
  return useQuery({
    queryKey: ['credential-types', key],
    queryFn: () => credentialsApi.getType(key!),
    enabled: !!key,
    staleTime: 5 * 60_000,
  })
}

export function useCredentials() {
  return useQuery({
    queryKey: KEY_LIST,
    queryFn: credentialsApi.list,
    staleTime: 30_000,
  })
}

export function useCredential(id: string | null | undefined) {
  return useQuery({
    queryKey: ['credentials', id],
    queryFn: () => credentialsApi.get(id!),
    enabled: !!id,
  })
}

export function useCreateCredential() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: CredentialCreateRequest) => credentialsApi.create(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY_LIST }),
  })
}

export function useUpdateCredential() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: CredentialUpdateRequest }) =>
      credentialsApi.update(id, data),
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({ queryKey: KEY_LIST })
      qc.invalidateQueries({ queryKey: ['credentials', id] })
    },
  })
}

export function useDeleteCredential() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => credentialsApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY_LIST })
      qc.invalidateQueries({ queryKey: ['tools'] })
    },
  })
}

export function useCredentialAuditLogs(id: string | null | undefined, limit = 50) {
  return useQuery({
    queryKey: ['credential-audit-logs', id, limit],
    queryFn: () => credentialsApi.listAuditLogs(id!, limit),
    enabled: !!id,
  })
}

// -- System credentials (operator-managed) ----------------------------------

export function useSystemCredentials() {
  return useQuery({
    queryKey: KEY_SYSTEM_LIST,
    queryFn: systemCredentialsApi.list,
    staleTime: 30_000,
  })
}

export function useCreateSystemCredential() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: CredentialCreateRequest) => systemCredentialsApi.create(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY_SYSTEM_LIST }),
  })
}

export function useUpdateSystemCredential() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: CredentialUpdateRequest }) =>
      systemCredentialsApi.update(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY_SYSTEM_LIST }),
  })
}

export function useDeleteSystemCredential() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => systemCredentialsApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY_SYSTEM_LIST }),
  })
}
