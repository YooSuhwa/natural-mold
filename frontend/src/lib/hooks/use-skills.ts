'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { skillsApi } from '@/lib/api/skills'
import type { SkillCreateRequest, SkillUpdateRequest } from '@/lib/types'

export function useSkills() {
  return useQuery({ queryKey: ['skills'], queryFn: skillsApi.list })
}

export function useSkill(id: string) {
  return useQuery({ queryKey: ['skills', id], queryFn: () => skillsApi.get(id), enabled: !!id })
}

export function useCreateSkill() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: SkillCreateRequest) => skillsApi.create(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['skills'] }),
  })
}

export function useUpdateSkill(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: SkillUpdateRequest) => skillsApi.update(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['skills'] })
      qc.invalidateQueries({ queryKey: ['skills', id] })
    },
  })
}

export function useDeleteSkill() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => skillsApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['skills'] }),
  })
}

export function useUploadSkill() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => skillsApi.upload(file),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['skills'] }),
  })
}
