'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { systemLlmSettingsApi } from '@/lib/api/system-llm-settings'
import type { SystemLlmRole, SystemLlmSettingUpdate } from '@/lib/types/system-llm-setting'

const KEY_LIST = ['system-llm-settings'] as const

export function useSystemLlmSettings() {
  return useQuery({
    queryKey: KEY_LIST,
    queryFn: systemLlmSettingsApi.list,
    staleTime: 30_000,
  })
}

export function useUpdateSystemLlmSetting() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ role, data }: { role: SystemLlmRole; data: SystemLlmSettingUpdate }) =>
      systemLlmSettingsApi.update(role, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY_LIST }),
  })
}
