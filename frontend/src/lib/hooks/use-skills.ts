'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { skillsApi } from '@/lib/api/skills'
import type {
  SkillContentUpdateRequest,
  SkillCreateRequest,
  SkillKind,
  SkillMetadataUpdateRequest,
} from '@/lib/types/skill'

const KEY_LIST = ['skills'] as const

export function useSkills(params?: { kind?: SkillKind; q?: string }) {
  return useQuery({
    queryKey: ['skills', params ?? {}],
    queryFn: () => skillsApi.list(params),
    staleTime: 30_000,
  })
}

export function useSkill(id: string | null | undefined) {
  return useQuery({
    queryKey: ['skills', id],
    queryFn: () => skillsApi.get(id!),
    enabled: !!id,
  })
}

export function useSkillFiles(id: string | null | undefined) {
  return useQuery({
    queryKey: ['skills', id, 'files'],
    queryFn: () => skillsApi.listFiles(id!),
    enabled: !!id,
  })
}

export function useSkillContent(id: string | null | undefined, enabled = true) {
  return useQuery({
    queryKey: ['skills', id, 'content'],
    queryFn: () => skillsApi.getContent(id!),
    enabled: !!id && enabled,
  })
}

export function useCreateTextSkill() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: SkillCreateRequest) => skillsApi.createText(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY_LIST }),
  })
}

export function useUploadPackageSkill() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => skillsApi.uploadPackage(file),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY_LIST }),
  })
}

export function useUpdateSkillMetadata() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: SkillMetadataUpdateRequest }) =>
      skillsApi.patchMetadata(id, data),
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({ queryKey: KEY_LIST })
      qc.invalidateQueries({ queryKey: ['skills', id] })
    },
  })
}

export function useUpdateSkillContent() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: SkillContentUpdateRequest }) =>
      skillsApi.putContent(id, data),
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({ queryKey: KEY_LIST })
      qc.invalidateQueries({ queryKey: ['skills', id] })
      qc.invalidateQueries({ queryKey: ['skills', id, 'content'] })
    },
  })
}

export function useDeleteSkill() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => skillsApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY_LIST }),
  })
}

export function useSetSkillFile(skillId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ path, content }: { path: string; content: string }) =>
      skillsApi.setFile(skillId, path, content),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY_LIST })
      qc.invalidateQueries({ queryKey: ['skills', skillId] })
      qc.invalidateQueries({ queryKey: ['skills', skillId, 'files'] })
    },
  })
}

export function useDeleteSkillFile(skillId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (path: string) => skillsApi.deleteFile(skillId, path),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY_LIST })
      qc.invalidateQueries({ queryKey: ['skills', skillId] })
      qc.invalidateQueries({ queryKey: ['skills', skillId, 'files'] })
    },
  })
}
