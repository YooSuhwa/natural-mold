'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { skillCredentialApi } from '@/lib/api/marketplace'
import { marketplaceQueryKeys } from '@/lib/query-keys/marketplace'
import { skillQueryKeys } from '@/lib/query-keys/skills'

import { requireQueryId } from './query-id'
import { skillEvaluationKeys } from './use-skill-evaluations'

export const skillCredentialBindingKeys = {
  requirements: (skillId: string | null | undefined) =>
    ['skills', skillId, 'credential-requirements'] as const,
  bindings: (skillId: string | null | undefined) =>
    ['skills', skillId, 'credential-bindings'] as const,
}

export function useSkillCredentialRequirements(skillId: string | null | undefined) {
  return useQuery({
    queryKey: skillCredentialBindingKeys.requirements(skillId),
    queryFn: () => skillCredentialApi.listRequirements(requireQueryId(skillId, 'skillId')),
    enabled: !!skillId,
  })
}

export function useSkillCredentialBindings(skillId: string | null | undefined) {
  return useQuery({
    queryKey: skillCredentialBindingKeys.bindings(skillId),
    queryFn: () => skillCredentialApi.listBindings(requireQueryId(skillId, 'skillId')),
    enabled: !!skillId,
  })
}

export function useSetSkillCredentialBinding(skillId: string) {
  const qc = useQueryClient()

  return useMutation({
    mutationFn: ({
      requirementKey,
      credentialId,
    }: {
      readonly requirementKey: string
      readonly credentialId: string
    }) => skillCredentialApi.setBinding(skillId, requirementKey, credentialId),
    onSuccess: () => {
      invalidateSkillCredentialBindingCaches(qc, skillId)
    },
  })
}

export function useDeleteSkillCredentialBinding(skillId: string) {
  const qc = useQueryClient()

  return useMutation({
    mutationFn: (requirementKey: string) =>
      skillCredentialApi.deleteBinding(skillId, requirementKey),
    onSuccess: () => {
      invalidateSkillCredentialBindingCaches(qc, skillId)
    },
  })
}

function invalidateSkillCredentialBindingCaches(
  qc: ReturnType<typeof useQueryClient>,
  skillId: string,
): void {
  qc.invalidateQueries({ queryKey: skillCredentialBindingKeys.bindings(skillId) })
  qc.invalidateQueries({ queryKey: skillQueryKeys.all })
  qc.invalidateQueries({ queryKey: skillQueryKeys.detail(skillId) })
  qc.invalidateQueries({ queryKey: marketplaceQueryKeys.items })
  qc.invalidateQueries({ queryKey: skillEvaluationKeys.sets(skillId) })
}
