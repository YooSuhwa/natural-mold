'use client'

import { useQuery } from '@tanstack/react-query'

import { skillUsageApi } from '@/lib/api/skill-usage'
import { skillQueryKeys } from '@/lib/query-keys/skills'
import { requireQueryId } from './query-id'

export function useSkillUsage(skillId: string | null | undefined, days = 30) {
  return useQuery({
    queryKey: skillQueryKeys.usage(skillId, days),
    queryFn: () => skillUsageApi.getSummary(requireQueryId(skillId, 'skillId'), days),
    enabled: !!skillId,
    staleTime: 30_000,
  })
}
